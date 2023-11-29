import pandas as pd
from pandas.testing import assert_frame_equal
from splink.duckdb.duckdb_comparison_library import (
    exact_match,
)
from splink.duckdb.linker import DuckDBLinker

df_1 = [
    {"unique_id": 1, "first_name": "Tom", "surname": "Fox", "dob": "1980-01-01"},
    {"unique_id": 2, "first_name": "Amy", "surname": "Lee", "dob": "1980-01-01"},
    {"unique_id": 3, "first_name": "Amy", "surname": "Lee", "dob": "1980-01-01"},
]

df_2 = [
    {"unique_id": 1, "first_name": "Bob", "surname": "Ray", "dob": "1999-09-22"},
    {"unique_id": 2, "first_name": "Amy", "surname": "Lee", "dob": "1980-01-01"},
]

df_1 = pd.DataFrame(df_1)
df_2 = pd.DataFrame(df_2)


def test_size_density_dedupe():
    settings = {
        "probability_two_random_records_match": 0.01,
        "link_type": "dedupe_only",
        "comparisons": [
            exact_match("first_name"),
            exact_match("surname"),
            exact_match("dob"),
        ],
    }
    linker = DuckDBLinker(df_1, settings)

    df_predict = linker.predict()
    df_clustered = linker.cluster_pairwise_predictions_at_threshold(df_predict, 0.9)

    df_result = linker._compute_cluster_metrics(
        df_predict, df_clustered, threshold_match_probability=0.9
    ).as_pandas_dataframe()

    data_expected = [
        {"cluster_id": 1, "n_nodes": 1, "n_edges": 0.0, "density": None},
        {"cluster_id": 2, "n_nodes": 2, "n_edges": 1.0, "density": 1.0},
    ]
    df_expected = pd.DataFrame(data_expected)

    assert_frame_equal(df_result, df_expected)


def test_size_density_link():
    settings = {
        "probability_two_random_records_match": 0.01,
        "link_type": "link_only",
        "comparisons": [
            exact_match("first_name"),
            exact_match("surname"),
            exact_match("dob"),
        ],
    }
    linker = DuckDBLinker(
        [df_1, df_2], settings, input_table_aliases=["df_left", "df_right"]
    )

    df_predict = linker.predict()
    df_clustered = linker.cluster_pairwise_predictions_at_threshold(df_predict, 0.9)

    df_result = (
        linker._compute_cluster_metrics(
            df_predict, df_clustered, threshold_match_probability=0.99
        )
        .as_pandas_dataframe()
        .sort_values(by="cluster_id")
    )

    data_expected = [
        {
            "cluster_id": "df_left-__-1",
            "n_nodes": 1,
            "n_edges": 0.0,
            "density": None,
        },
        {
            "cluster_id": "df_right-__-1",
            "n_nodes": 1,
            "n_edges": 0.0,
            "density": None,
        },
        {
            "cluster_id": "df_left-__-2",
            "n_nodes": 3,
            "n_edges": 2.0,
            "density": 0.666667,
        },
    ]
    df_expected = pd.DataFrame(data_expected).sort_values(by="cluster_id")

    assert_frame_equal(df_result, df_expected)
