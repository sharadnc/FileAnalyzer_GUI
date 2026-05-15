"""Tests for :mod:`file_analyzer.pivot_hierarchy`."""

from __future__ import annotations

import duckdb

from file_analyzer.pivot_hierarchy import build_excel_style_pivot_table, build_pivot_leaf_sql


def test_build_pivot_leaf_sql_single_row_dim() -> None:
    """Purpose: ``build_pivot_leaf_sql`` should produce valid DuckDB for one row dim.

    Internal Logic
    ---------------
    Compile SQL with one row dimension, no column dimensions, one measure, run on
    an in-memory table, and assert one grouped row.

    Example invocation
    --------------------
    ``pytest tests/test_pivot_hierarchy.py::test_build_pivot_leaf_sql_single_row_dim -q``
    """

    sql = build_pivot_leaf_sql(
        table_name="t_leaf",
        base_where_sql="",
        row_dims=("a",),
        col_dims=(),
        measures=("v",),
        agg="SUM",
    )
    con = duckdb.connect()
    con.execute("CREATE TABLE t_leaf(a VARCHAR, v DOUBLE)")
    con.executemany("INSERT INTO t_leaf VALUES (?, ?)", [("x", 1.0), ("x", 2.0)])
    rows = con.execute(sql).fetchall()
    assert rows == [("x", 3.0)]


def test_excel_style_pivot_two_row_dims_and_column_dim() -> None:
    """Purpose: hierarchy walk emits subtotals, details, and grand row.

    Internal Logic
    ---------------
    Hand-build leaf rows (SUMLEV, DIVISION, REGION, measure), run
    :func:`build_excel_style_pivot_table`, and assert row kinds include ``subtotal``,
    ``detail``, and ``grand``.

    Example invocation
    --------------------
    ``pytest tests/test_pivot_hierarchy.py::test_excel_style_pivot_two_row_dims_and_column_dim -q``
    """

    leaves = [
        ("40", "5", "3", 10.0),
        ("40", "5", "4", 1.0),
        ("40", "7", "3", 5.0),
    ]
    cols, rows, kinds, depths, expandable, _sql = build_excel_style_pivot_table(
        leaf_rows=leaves,
        row_dims=("SUMLEV", "DIVISION"),
        col_dims=("REGION",),
        measures=("POP",),
        agg="SUM",
        sql_executed="(test)",
    )
    assert cols[-1] == "GRAND TOTAL"
    assert "subtotal" in kinds
    assert "detail" in kinds
    assert kinds[-1] == "grand"
    assert len(rows) == len(kinds) == len(depths) == len(expandable)
    assert any(expandable), "at least one subtotal row should be expandable with nested rows"
    assert "REGION-3" in cols
    assert "REGION-4" in cols
    assert "POP [3]" not in cols
    assert "POP Grand Total" not in cols


def test_no_column_dims_measure_values_not_zeroed() -> None:
    """Purpose: without column dimensions, measure cells must match leaf aggregates.

    Internal Logic
    ----------------
    Regression: leaf keys used ``__TOTAL__`` internally but ``sorted_cols`` used
    ``Total``, so :func:`_numeric_block_full` looked up the wrong key and showed zeros.

    Example invocation
    --------------------
    ``pytest tests/test_pivot_hierarchy.py::test_no_column_dims_measure_values_not_zeroed -q``
    """

    leaves = [
        ("040", "05", "SomePlace", 123.45),
    ]
    _cols, rows, kinds, _, _, _ = build_excel_style_pivot_table(
        leaf_rows=leaves,
        row_dims=("SUMLEV", "DIVISION", "NAME"),
        col_dims=(),
        measures=("POPESTIMATE2020",),
        agg="SUM",
        sql_executed="(test)",
    )
    detail_rows = [r for r, k in zip(rows, kinds) if k == "detail"]
    assert len(detail_rows) == 1
    tail = list(detail_rows[0][3:])
    assert 123.45 in tail, tail


def test_multi_measure_omits_row_grand_total_columns() -> None:
    """Purpose: with 2+ measures, omit horizontal ``GRAND TOTAL`` columns.

    Internal Logic
    ----------------
    Build leaves with two measures and one column dimension; assert ``cols`` contains
    no ``GRAND TOTAL`` header (row sums across buckets mix incomparable measures).

    Example invocation
    --------------------
    ``pytest tests/test_pivot_hierarchy.py::test_multi_measure_omits_row_grand_total_columns -q``
    """

    leaves = [
        ("40", "5", "3", 1.0, 10.0),
        ("40", "5", "4", 2.0, 20.0),
    ]
    cols, rows, kinds, _, _, _ = build_excel_style_pivot_table(
        leaf_rows=leaves,
        row_dims=("SUMLEV", "DIVISION"),
        col_dims=("REGION",),
        measures=("POP", "EST"),
        agg="SUM",
        sql_executed="(test)",
    )
    assert "GRAND TOTAL" not in cols
    detail = [r for r, k in zip(rows, kinds) if k == "detail"]
    assert len(detail) == 1
    # Two row labels + 2 regions × 2 measures = 6 numeric cells (no trailing grand cols).
    assert len(detail[0]) == 2 + 4
    assert detail[0][2:6] == [1.0, 2.0, 10.0, 20.0]
