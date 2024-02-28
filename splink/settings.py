from __future__ import annotations

import logging
from copy import copy, deepcopy
from dataclasses import asdict, dataclass
from typing import List

from .blocking import BlockingRule, SaltedBlockingRule, blocking_rule_to_obj
from .charts import m_u_parameters_chart, match_weights_chart
from .comparison import Comparison
from .comparison_level import ComparisonLevel
from .input_column import InputColumn
from .misc import dedupe_preserving_order, prob_to_bayes_factor, prob_to_match_weight
from .parse_sql import get_columns_used_from_sql

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColumnInfoSettings:
    bayes_factor_column_prefix: str
    term_frequency_adjustment_column_prefix: str
    comparison_vector_value_column_prefix: str
    unique_id_column_name: str
    _source_dataset_column_name: str
    _source_dataset_column_name_is_required: str
    sql_dialect: str

    @property
    def source_dataset_column_name(self):
        if self._source_dataset_column_name_is_required:
            return self._source_dataset_column_name
        else:
            return None

    @property
    def unique_id_input_columns(self) -> list[InputColumn]:
        cols = []

        if source_dataset_column_name := (self.source_dataset_column_name):
            col = InputColumn(
                source_dataset_column_name,
                column_info_settings=self,
                sql_dialect=self.sql_dialect,
            )
            cols.append(col)

        col = InputColumn(
            self.unique_id_column_name,
            column_info_settings=self,
            sql_dialect=self.sql_dialect,
        )
        cols.append(col)

        return cols

    def as_dict(self) -> dict:
        full_dict = self._as_full_dict()
        full_dict["source_dataset_column_name"] = self._source_dataset_column_name
        del full_dict["_source_dataset_column_name"]
        del full_dict["_source_dataset_column_name_is_required"]
        return full_dict

    def _as_full_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrainingSettings:
    em_convergence: float
    max_iterations: int
    training_mode: bool
    blocking_rule_for_training: BlockingRule
    estimate_without_term_frequencies: bool

    def as_dict(self) -> dict:
        # TODO: we can remove estimate_without_term_frequencies if we want
        naive_dict = asdict(self)
        if br := self.blocking_rule_for_training:
            naive_dict["blocking_rule_for_training"] = copy(br)
        return naive_dict


@dataclass
class CoreModelSettings:
    comparisons: List[Comparison]
    probability_two_random_records_match: float

    def copy(self):
        """Returns a deepcopy of CoreModelSettings"""
        return deepcopy(self)

    @property
    def parameters_as_detailed_records(self):
        output = []
        rr_match = self.probability_two_random_records_match
        for i, cc in enumerate(self.comparisons):
            records = cc._as_detailed_records
            for r in records:
                r["probability_two_random_records_match"] = rr_match
                r["comparison_sort_order"] = i
            output.extend(records)

        prior_description = (
            "The probability that two random records drawn at random match is "
            f"{rr_match:.3f} or one in "
            f" {1/rr_match:,.1f} records."
            "This is equivalent to a starting match weight of "
            f"{prob_to_match_weight(rr_match):.3f}."
        )

        # Finally add a record for probability_two_random_records_match
        prop_record = {
            "comparison_name": "probability_two_random_records_match",
            "sql_condition": None,
            "label_for_charts": "",
            "m_probability": None,
            "u_probability": None,
            "m_probability_description": None,
            "u_probability_description": None,
            "has_tf_adjustments": False,
            "tf_adjustment_column": None,
            "tf_adjustment_weight": None,
            "is_null_level": False,
            "bayes_factor": prob_to_bayes_factor(rr_match),
            "log2_bayes_factor": prob_to_match_weight(rr_match),
            "comparison_vector_value": 0,
            "max_comparison_vector_value": 0,
            "bayes_factor_description": prior_description,
            "probability_two_random_records_match": rr_match,
            "comparison_sort_order": -1,
        }
        output.insert(0, prop_record)
        return output


class Settings:
    """The settings object contains the configuration and parameters of the data
    linking model"""

    def __init__(
        self,
        link_type: str,
        *,
        # TODO: make everything compulsory at this level?
        comparisons: List[Comparison] = [],
        blocking_rules_to_generate_predictions: List[BlockingRule] = [],
        probability_two_random_records_match: float = 0.0001,
        retain_matching_columns: bool = True,
        retain_intermediate_calculation_columns: bool = False,
        additional_columns_to_retain: List[str] = [],
        # ColumnInfoSettings
        unique_id_column_name: str = "unique_id",
        source_dataset_column_name: str = "source_dataset",
        bayes_factor_column_prefix: str = "bf_",
        term_frequency_adjustment_column_prefix: str = "tf_",
        comparison_vector_value_column_prefix: str = "gamma_",
        # TrainingSettings
        em_convergence: float = 0.0001,
        max_iterations: int = 25,
        # TODO: do we need this long-term?
        training_mode: bool = False,
        blocking_rule_for_training: BlockingRule = None,
        estimate_without_term_frequencies: bool = False,
        # other
        sql_dialect: str = None,
        linker_uid: str = None,
    ):
        # TODO: hook up validation here
        # Validate against schema before processing
        # validate_settings_against_schema(settings_dict)

        self._sql_dialect = sql_dialect
        self._link_type = link_type

        self.column_info_settings = ColumnInfoSettings(
            comparison_vector_value_column_prefix=comparison_vector_value_column_prefix,
            bayes_factor_column_prefix=bayes_factor_column_prefix,
            term_frequency_adjustment_column_prefix=term_frequency_adjustment_column_prefix,
            unique_id_column_name=unique_id_column_name,
            _source_dataset_column_name=source_dataset_column_name,
            # TODO: if we want this to keep in-sync with link type, can put logic in
            # link_type setter
            _source_dataset_column_name_is_required=self._get_source_dataset_column_name_is_required(),
            sql_dialect=sql_dialect,
        )

        comps = []
        for comparison in comparisons:
            comparison.column_info_settings = self.column_info_settings
            comps.append(comparison)

        self.core_model_settings = CoreModelSettings(
            comparisons=comps,
            probability_two_random_records_match=probability_two_random_records_match,
        )

        self.training_settings = TrainingSettings(
            em_convergence=em_convergence,
            max_iterations=max_iterations,
            # TODO: can we factor these out?
            blocking_rule_for_training=blocking_rule_for_training,
            training_mode=training_mode,
            estimate_without_term_frequencies=estimate_without_term_frequencies,
        )

        self._retain_matching_columns = retain_matching_columns
        self._retain_intermediate_calculation_columns = (
            retain_intermediate_calculation_columns
        )

        # TODO: do we need to convert?
        self._blocking_rules_to_generate_predictions = self._brs_as_objs(
            blocking_rules_to_generate_predictions
        )

        self._cache_uid = linker_uid

        self._warn_if_no_null_level_in_comparisons()

        self._additional_col_names_to_retain = additional_columns_to_retain

    def __deepcopy__(self, memo) -> Settings:
        """When we do EM training, we need a copy of the Settings which is independent
        of the original e.g. modifying the copy will not affect the original.
        This method implements ensures the Settings can be deepcopied."""
        cc = Settings(**self._as_dict_for_copying())
        return cc

    # TODO: move this to Comparison
    def _warn_if_no_null_level_in_comparisons(self):
        for c in self.comparisons:
            if not c._has_null_level:
                logger.warning(
                    "Warning: No null level found for comparison "
                    f"{c.output_column_name}.\n"
                    "In most cases you want to define a comparison level that deals"
                    " with the case that one or both sides of the comparison are null."
                    "\nThis comparison level should have the `is_null_level` flag to "
                    "True in the settings for that comparison level"
                    "\nIf the column does not contain null values, or you know what "
                    "you're doing, you can ignore this warning"
                )

    # TODO: unpick these four
    @property
    def comparisons(self) -> List[Comparison]:
        return self.core_model_settings.comparisons

    @property
    def _probability_two_random_records_match(self) -> float:
        return self.core_model_settings.probability_two_random_records_match

    # TODO: especially factor these out
    @comparisons.setter
    def comparisons(self, value) -> None:
        self.core_model_settings.comparisons = value

    @_probability_two_random_records_match.setter
    def _probability_two_random_records_match(self, value) -> None:
        self.core_model_settings.probability_two_random_records_match = value

    @property
    def _additional_column_names_to_retain(self) -> List[str]:
        cols_to_retain = []

        # Add any columns used in blocking rules but not model
        if self._retain_matching_columns:
            # Want to add any columns not already by the model
            used_by_brs = []
            for br in self._blocking_rules_to_generate_predictions:
                used_by_brs.extend(
                    get_columns_used_from_sql(br.blocking_rule_sql, br.sql_dialect)
                )

            used_by_brs = [InputColumn(c) for c in used_by_brs]

            used_by_brs = [c.unquote().name for c in used_by_brs]
            already_used = self._columns_used_by_comparisons
            already_used = [InputColumn(c) for c in already_used]
            already_used = [c.unquote().name for c in already_used]

            new_cols = list(set(used_by_brs) - set(already_used))
            cols_to_retain.extend(new_cols)

        cols_to_retain.extend(self._additional_col_names_to_retain)
        return cols_to_retain

    @property
    def _additional_columns_to_retain(self) -> List[InputColumn]:
        cols = self._additional_column_names_to_retain
        return [
            InputColumn(
                c,
                column_info_settings=self.column_info_settings,
                sql_dialect=self._sql_dialect,
            )
            for c in cols
        ]

    def _get_source_dataset_column_name_is_required(self):
        return self._link_type not in ["dedupe_only"]

    @property
    def _term_frequency_columns(self) -> list[InputColumn]:
        cols = set()
        for cc in self.comparisons:
            cols.update(cc._tf_adjustment_input_col_names)
        return [
            InputColumn(
                c,
                column_info_settings=self.column_info_settings,
                sql_dialect=self._sql_dialect,
            )
            for c in list(cols)
        ]

    @property
    def _needs_matchkey_column(self) -> bool:
        """Where multiple `blocking_rules_to_generate_predictions` are specified,
        it's useful to include a matchkey column, that indicates from which blocking
        rule the pairwise record comparisons arose.

        This column is only needed if multiple rules are specified.
        """

        return len(self._blocking_rules_to_generate_predictions) > 1

    @property
    def _columns_used_by_comparisons(self) -> List[str]:
        cols_used = []
        for uid_col in self.column_info_settings.unique_id_input_columns:
            cols_used.append(uid_col.name)
        for cc in self.comparisons:
            cols = cc._input_columns_used_by_case_statement
            cols = [c.name for c in cols]

            cols_used.extend(cols)
        return dedupe_preserving_order(cols_used)

    @property
    def _columns_to_select_for_blocking(self) -> List[str]:
        cols = []

        for uid_col in self.column_info_settings.unique_id_input_columns:
            cols.extend(uid_col.l_r_names_as_l_r)

        for cc in self.comparisons:
            cols.extend(cc._columns_to_select_for_blocking())

        for add_col in self._additional_columns_to_retain:
            cols.extend(add_col.l_r_names_as_l_r)

        return dedupe_preserving_order(cols)

    @property
    def _columns_to_select_for_comparison_vector_values(self) -> List[str]:
        cols = []

        for uid_col in self.column_info_settings.unique_id_input_columns:
            cols.extend(uid_col.names_l_r)

        for cc in self.comparisons:
            cols.extend(
                cc._columns_to_select_for_comparison_vector_values(
                    self._retain_matching_columns
                )
            )

        for add_col in self._additional_columns_to_retain:
            cols.extend(add_col.names_l_r)

        if self._needs_matchkey_column:
            cols.append("match_key")

        cols = dedupe_preserving_order(cols)
        return cols

    @property
    def _columns_to_select_for_bayes_factor_parts(self) -> List[str]:
        cols = []

        for uid_col in self.column_info_settings.unique_id_input_columns:
            cols.extend(uid_col.names_l_r)

        for cc in self.comparisons:
            cols.extend(
                cc._columns_to_select_for_bayes_factor_parts(
                    self._retain_matching_columns,
                    self._retain_intermediate_calculation_columns,
                )
            )

        for add_col in self._additional_columns_to_retain:
            cols.extend(add_col.names_l_r)

        if self._needs_matchkey_column:
            cols.append("match_key")

        cols = dedupe_preserving_order(cols)
        return cols

    @property
    def _columns_to_select_for_predict(self) -> List[str]:
        cols = []

        for uid_col in self.column_info_settings.unique_id_input_columns:
            cols.append(uid_col.name_l)
            cols.append(uid_col.name_r)

        for cc in self.comparisons:
            cols.extend(
                cc._columns_to_select_for_predict(
                    self._retain_matching_columns,
                    self._retain_intermediate_calculation_columns,
                    self.training_settings.training_mode,
                )
            )

        for add_col in self._additional_columns_to_retain:
            cols.extend(add_col.names_l_r)

        if self._needs_matchkey_column:
            cols.append("match_key")

        cols = dedupe_preserving_order(cols)
        return cols

    def _get_comparison_by_output_column_name(self, name):
        for cc in self.comparisons:
            if cc.output_column_name == name:
                return cc
        raise ValueError(f"No comparison column with name {name}")

    def _brs_as_objs(self, brs_as_strings) -> List[BlockingRule]:
        brs_as_objs = [blocking_rule_to_obj(br) for br in brs_as_strings]
        for n, br in enumerate(brs_as_objs):
            br.add_preceding_rules(brs_as_objs[:n])
        return brs_as_objs

    def _get_comparison_levels_corresponding_to_training_blocking_rule(
        self, blocking_rule
    ) -> dict[str, Comparison | ComparisonLevel]:
        """
        If we block on (say) first name and surname, then all blocked comparisons are
        guaranteed to have a match on first name and surname

        The probability two random records match must be adjusted for the fact this is a
        subset of the comparisons

        To correctly adjust, we need to find one or more comparison levels corresponding
        to the blocking rule and use their bayes factor

        In the example, we need to find a comparison level for an exact match on first
        name, and one for an exact match on surname

        Or alternatively (and preferably, to avoid correlation issues), a comparison
        level for an exact match on first_name AND surname.   i.e. a single level for
        exact match on full name

        """
        blocking_exact_match_columns = set(
            get_columns_used_from_sql(
                blocking_rule,
                dialect=self._sql_dialect,
            )
        )

        ccs = self.comparisons

        exact_comparison_levels = []
        for cc in ccs:
            for cl in cc.comparison_levels:
                if cl._is_exact_match:
                    exact_comparison_levels.append({"level": cl, "comparison": cc})

        # Where exact match on multiple columns exists, use that instead of individual
        # exact match columns
        # So for example, if we have a param estimate for exact match on first name AND
        # surname, prefer that
        # over individual estimtes for exact match first name and surname.
        exact_comparison_levels.sort(
            key=lambda x: -len(x["level"]._exact_match_colnames)
        )

        comparison_levels_corresponding_to_blocking_rule = []
        for level_info in exact_comparison_levels:
            cl = level_info["level"]
            exact_cols = set(cl._exact_match_colnames)
            if exact_cols.issubset(blocking_exact_match_columns):
                blocking_exact_match_columns = blocking_exact_match_columns - exact_cols
                comparison_levels_corresponding_to_blocking_rule.append(level_info)

        return comparison_levels_corresponding_to_blocking_rule

    # TODO: we can probably unhook this
    @property
    def _parameters_as_detailed_records(self):
        return self.core_model_settings.parameters_as_detailed_records

    @property
    def _parameter_estimates_as_records(self):
        output = []
        for i, cc in enumerate(self.comparisons):
            records = cc._parameter_estimates_as_records
            for r in records:
                r["comparison_sort_order"] = i
            output.extend(records)
        return output

    def _simple_dict_entries(self) -> dict:
        return {
            "link_type": self._link_type,
            "probability_two_random_records_match": (
                self._probability_two_random_records_match
            ),
            "retain_matching_columns": self._retain_matching_columns,
            "retain_intermediate_calculation_columns": (
                self._retain_intermediate_calculation_columns
            ),
            "additional_columns_to_retain": self._additional_col_names_to_retain,
            "sql_dialect": self._sql_dialect,
            "linker_uid": self._cache_uid,
            **self.training_settings.as_dict(),
            **self.column_info_settings.as_dict(),
        }

    # TODO: once more settled, simplify the serialisation logic
    def as_dict(self):
        """Serialise the current settings (including any estimated model parameters)
        to a dictionary, enabling the settings to be saved to disk and reloaded
        """
        brs = self._blocking_rules_to_generate_predictions
        current_settings = {
            "blocking_rules_to_generate_predictions": [br.as_dict() for br in brs],
            "comparisons": [cc.as_dict() for cc in self.comparisons],
            "additional_columns_to_retain": self._additional_col_names_to_retain,
        }
        current_settings = {
            **self._simple_dict_entries(),
            **current_settings,
        }
        del current_settings["training_mode"]
        del current_settings["blocking_rule_for_training"]
        del current_settings["estimate_without_term_frequencies"]
        return current_settings

    def _as_completed_dict(self):
        brs = self._blocking_rules_to_generate_predictions
        current_settings = {
            "blocking_rules_to_generate_predictions": [
                br._as_completed_dict() for br in brs
            ],
            "comparisons": [cc._as_completed_dict() for cc in self.comparisons],
        }
        return {
            **self._simple_dict_entries(),
            **current_settings,
        }

    def _as_dict_for_copying(self):
        return {
            **self._simple_dict_entries(),
            "comparisons": [deepcopy(c) for c in self.comparisons],
            "blocking_rules_to_generate_predictions": (
                # BlockingRules are simple, so only need a shallow copy
                # TODO: can/should we rely on this?
                [copy(br) for br in self._blocking_rules_to_generate_predictions]
            ),
        }

    def match_weights_chart(self, as_dict=False):
        records = self._parameters_as_detailed_records

        return match_weights_chart(records, as_dict=as_dict)

    def m_u_parameters_chart(self, as_dict=False):
        records = self._parameters_as_detailed_records
        return m_u_parameters_chart(records, as_dict=as_dict)

    def _columns_without_estimated_parameters_message(self):
        message_lines = []
        for c in self.comparisons:
            msg = c._is_trained_message
            if msg is not None:
                message_lines.append(c._is_trained_message)

        if len(message_lines) == 0:
            message = (
                "\nYour model is fully trained. All comparisons have at least "
                "one estimate for their m and u values"
            )
        else:
            message = "\nYour model is not yet fully trained. Missing estimates for:"
            message_lines.insert(0, message)
            message = "\n".join(message_lines)

        logger.info(message)

    # TODO: use property + raw None value instead?
    @property
    def _lambda_is_default(self):
        if self._probability_two_random_records_match == 0.0001:
            return True
        else:
            return False

    @property
    def _is_fully_trained(self):
        return all([c._is_trained for c in self.comparisons])

    def _not_trained_messages(self):
        messages = []
        for c in self.comparisons:
            messages.extend(c._not_trained_messages)
        if self._lambda_is_default:
            messages.extend(
                [
                    "The 'probability_two_random_records_match' setting has been set to"
                    " the default value (0.0001). \nIf this is not the desired "
                    "behaviour, either: \n - assign a value for "
                    "`probability_two_random_records_match` in your settings dictionary"
                    ", or \n - estimate with the"
                    " `linker.estimate_probability_two_random_records_match` function."
                ]
            )
        return messages

    @property
    def human_readable_description(self):
        comparison_descs = [
            c._human_readable_description_succinct for c in self.comparisons
        ]
        comparison_descs = "\n".join(comparison_descs)
        desc = (
            "SUMMARY OF LINKING MODEL\n"
            "------------------------\n"
            "The similarity of pairwise record comparison in your model will be "
            f"assessed as follows:\n\n{comparison_descs}"
        )
        return desc

    @property
    def salting_required(self):
        for br in self._blocking_rules_to_generate_predictions:
            if isinstance(br, SaltedBlockingRule):
                return True
        return False
