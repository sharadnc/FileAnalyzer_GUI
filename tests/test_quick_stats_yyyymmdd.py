"""Tests for YYYYMMDD-formatted quick stats on date-like fields."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from file_analyzer.config import load_app_config
from file_analyzer.duckdb_session import DuckDBSession, DuckDBSessionConfig
from file_analyzer.meta_parser import (
    FieldMeta,
    MetaDefinition,
    field_quick_stats_use_yyyymmdd_values,
    parse_meta_from_excel,
)
from file_analyzer.stats_service import compute_quick_stats_parallel, quick_stats_type_bucket
from file_analyzer.ui.quick_stats_tooltips import build_tooltip_html


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_field_quick_stats_use_yyyymmdd_values_for_date_and_yyyymmdd_types() -> None:
    """Purpose: Date/Datetime storage types and YYYYMMDD role use compact dates in quick stats."""

    ymd = FieldMeta("DUE_DT", "YYYYMMDD", None, "Due", "M")
    dt = FieldMeta("TXN_DT", "Datetime", None, "Txn", "D")
    d = FieldMeta("CLOSE_DT", "Date", None, "Close", "D")
    assert field_quick_stats_use_yyyymmdd_values(ymd)
    assert field_quick_stats_use_yyyymmdd_values(dt)
    assert field_quick_stats_use_yyyymmdd_values(d)


def test_quick_stats_type_bucket_date_types_use_char_frequencies() -> None:
    f = FieldMeta("X", "Date", None, "x", "D")
    assert quick_stats_type_bucket(f) == "char"


def test_compute_quick_stats_formats_date_field_as_yyyymmdd(tmp_path: Path) -> None:
    """Purpose: Top-value counts show eight-digit dates, not ISO strings."""

    field = FieldMeta("DUE_DT", "Date", None, "Due date", "D")
    md = MetaDefinition(file_key_columns=[], fields=[field])
    db_path = tmp_path / "dates.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE data AS
            SELECT * FROM (VALUES
              (DATE '2026-01-31'),
              (DATE '2026-01-31'),
              (DATE '2022-02-28')
            ) AS t("DUE_DT")
            """
        )
    finally:
        con.close()

    stats = compute_quick_stats_parallel(md, str(db_path), "data", top_n=5, max_workers=1)
    freqs = stats["DUE_DT"].char_frequencies
    assert freqs is not None
    values = {v for v, _ in freqs}
    assert "20260131" in values
    assert "20220228" in values
    assert not any("-" in v for v in values)


def test_loanpop_due_dt_quick_stats_use_yyyymmdd() -> None:
    """Purpose: Default LoanPop ``DUE_DT`` (FieldType YYYYMMDD) shows compact dates on hover."""

    meta_path = _repo_root() / "templates" / "LoanPop.xlsx"
    data_path = _repo_root() / "sample" / "LoanPop_small.txt"
    if not meta_path.exists() or not data_path.exists():
        pytest.skip("LoanPop sample files not present")

    cfg = load_app_config()
    meta = parse_meta_from_excel(meta_path)
    session = DuckDBSession(
        meta,
        DuckDBSessionConfig(
            temp_base_dir=cfg.temp_base_dir,
            duckdb_storage_mode=cfg.duckdb_storage_mode,
            duckdb_threads=cfg.duckdb_threads,
            cleanup_on_close=False,
        ),
    )
    session.load_csv_as_table(data_path, "|", "data", True)
    stats = compute_quick_stats_parallel(meta, str(session.database_path), "data", 10, 2)
    session.close()

    due = stats.get("DUE_DT")
    assert due is not None
    assert due.char_frequencies is not None
    assert all(len(v) == 8 and v.isdigit() for v, _ in due.char_frequencies)
    html = build_tooltip_html(due, 2)
    assert "NULL" in html
    assert any(v in html for v, _ in due.char_frequencies)
