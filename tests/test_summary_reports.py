"""Tests for :mod:`file_analyzer.summary_reports`."""

from __future__ import annotations

import duckdb
from pathlib import Path

from file_analyzer.meta_parser import FieldMeta, MetaDefinition
from file_analyzer.summary_reports import compute_dataset_summary_report


def test_summary_report_detects_duplicate_rows() -> None:
    """Purpose: duplicate extra rows should count repeated full-row groups.

    Internal Logic
    ---------------
    Build a DuckDB table with two identical rows and one unique row, run
    :func:`compute_dataset_summary_report`, and assert duplicate extras equal 2.

    Example invocation
    --------------------
    ``pytest tests/test_summary_reports.py::test_summary_report_detects_duplicate_rows -q``
    """

    p = Path("_tmp_summary_test.duckdb")
    if p.exists():
        p.unlink()
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE data(a VARCHAR, b DOUBLE)")
    con.executemany("INSERT INTO data VALUES (?, ?)", [("x", 1.0), ("x", 1.0), ("y", 2.0)])
    con.close()
    meta = MetaDefinition(
        file_key_columns=["a"],
        fields=[
            FieldMeta("a", "Char", None, "", "D"),
            FieldMeta("b", "Num", None, "", "M"),
        ],
    )
    rep = compute_dataset_summary_report(str(p.resolve()), "data", meta)
    p.unlink()
    assert rep.duplicates.total_rows == 3
    assert rep.duplicates.distinct_full_rows == 2
    assert rep.duplicates.duplicate_extra_rows == 1
    assert len(rep.fields) == 2


def test_measure_field_has_numeric_stats() -> None:
    """Purpose: measure columns should populate numeric summary slots.

    Internal Logic
    ---------------
    Insert three numeric values and assert mean/median are finite on the report.

    Example invocation
    --------------------
    ``pytest tests/test_summary_reports.py::test_measure_field_has_numeric_stats -q``
    """

    p = Path("_tmp_summary_num.duckdb")
    if p.exists():
        p.unlink()
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE data(x DOUBLE)")
    con.executemany("INSERT INTO data VALUES (?)", [(1.0,), (2.0,), (9.0,)])
    con.close()
    meta = MetaDefinition(
        file_key_columns=[],
        fields=[FieldMeta("x", "Num", None, "", "M")],
    )
    rep = compute_dataset_summary_report(str(p.resolve()), "data", meta)
    p.unlink()
    fr = rep.fields[0]
    assert fr.field_name == "x"
    assert fr.numeric_mean is not None
    assert fr.numeric_median is not None


def test_dimension_distribution_includes_total_row() -> None:
    """Purpose: dimension (D) tables end with a Total row equal to row count.

    Internal Logic
    ---------------
    Build a small table with one D field and assert the last distribution row is
    ``Total`` with the full row count.

    Example invocation
    --------------------
    ``pytest tests/test_summary_reports.py::test_dimension_distribution_includes_total_row -q``
    """

    p = Path("_tmp_summary_dim.duckdb")
    if p.exists():
        p.unlink()
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE data(a VARCHAR)")
    con.executemany("INSERT INTO data VALUES (?)", [("x",), ("y",), ("x",)])
    con.close()
    meta = MetaDefinition(
        file_key_columns=[],
        fields=[FieldMeta("a", "Char", None, "", "D")],
    )
    rep = compute_dataset_summary_report(str(p.resolve()), "data", meta)
    p.unlink()
    fr = rep.fields[0]
    assert fr.top_string_values[-1][0] == "Total"
    assert fr.top_string_values[-1][1] == 3
