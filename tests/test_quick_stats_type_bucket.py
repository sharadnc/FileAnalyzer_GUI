"""Tests for quick-stats field-type bucketing (Excel ``D``/``M`` FieldType support)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from file_analyzer.meta_parser import FieldMeta, MetaDefinition, parse_meta_from_excel
from file_analyzer.stats_service import compute_quick_stats_parallel, quick_stats_type_bucket


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_quick_stats_type_bucket_excel_dimension_fieldtype_d() -> None:
    """Purpose: Excel ``FieldType`` ``D`` maps to categorical (char) quick stats.

    Example invocation
    ------------------
    ``quick_stats_type_bucket(FieldMeta(..., field_type=\"D\", field_dtype=\"D\"))`` → ``\"char\"``.
    """

    field = FieldMeta(
        name="STATE",
        field_type="D",
        field_length=None,
        description="State",
        field_dtype="D",
    )
    assert quick_stats_type_bucket(field) == "char"


def test_quick_stats_type_bucket_excel_measure_fieldtype_m() -> None:
    field = FieldMeta(
        name="LOAN_AMT",
        field_type="M",
        field_length="12",
        description="Loan Amount",
        field_dtype="M",
    )
    assert quick_stats_type_bucket(field) == "num"


def test_quick_stats_type_bucket_skips_display() -> None:
    field = FieldMeta(
        name="LOAN_NBR",
        field_type="DISPLAY",
        field_length=None,
        description="Loan Number",
        field_dtype="D",
    )
    assert quick_stats_type_bucket(field) == "skip"


def test_loanpop_excel_meta_state_fieldtype_is_dimension_role() -> None:
    """Purpose: Shipped ``LoanPop.xlsx`` marks ``STATE`` with Excel ``FieldType`` ``D``."""

    meta_path = _repo_root() / "templates" / "LoanPop.xlsx"
    if not meta_path.exists():
        pytest.skip("LoanPop.xlsx template not present")

    state = parse_meta_from_excel(meta_path).fields_by_name["STATE"]
    assert state.field_type == "D"
    assert quick_stats_type_bucket(state) == "char"


def test_loanpop_excel_meta_loan_amt_fieldtype_is_measure_role() -> None:
    """Purpose: Default ``LoanPop.xlsx`` marks ``LOAN_AMT`` with Excel ``FieldType`` ``M`` (not ``Num``)."""

    meta_path = _repo_root() / "templates" / "LoanPop.xlsx"
    if not meta_path.exists():
        pytest.skip("LoanPop.xlsx template not present")

    loan_amt = parse_meta_from_excel(meta_path).fields_by_name["LOAN_AMT"]
    assert loan_amt.field_type == "M"
    assert loan_amt.field_dtype == "M"
    assert quick_stats_type_bucket(loan_amt) == "num"


def test_compute_quick_stats_includes_excel_fieldtype_d_and_m(tmp_path: Path) -> None:
    """Purpose: Parallel quick stats run for Excel ``FieldType`` ``D`` and ``M`` fields."""

    md = MetaDefinition(
        file_key_columns=["STATE"],
        fields=[
            FieldMeta("STATE", "D", None, "State", "D"),
            FieldMeta("LOAN_AMT", "M", "12", "Loan Amount", "M"),
        ],
    )
    db_path = tmp_path / "qstats.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE data AS
            SELECT * FROM (VALUES
              ('CA', 100.0),
              ('NY', 200.0),
              ('CA', 150.0)
            ) AS t("STATE", "LOAN_AMT")
            """
        )
    finally:
        con.close()

    stats = compute_quick_stats_parallel(
        meta=md,
        database_path=str(db_path),
        table_name="data",
        top_n=5,
        max_workers=2,
    )
    assert stats["STATE"].char_frequencies is not None
    assert stats["STATE"].char_frequencies[0][0] == "CA"
    assert stats["LOAN_AMT"].numeric_summary is not None
    assert stats["LOAN_AMT"].numeric_summary["max"] == 200.0
