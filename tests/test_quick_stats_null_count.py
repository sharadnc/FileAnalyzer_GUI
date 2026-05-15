"""Tests for mandatory NULL counts in quick stats."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from file_analyzer.config import load_app_config
from file_analyzer.meta_parser import MetaDefinition, parse_meta_from_excel
from file_analyzer.duckdb_session import DuckDBSession, DuckDBSessionConfig
from file_analyzer.stats_service import FieldQuickStats, compute_quick_stats_parallel
from file_analyzer.ui.visualize_tab import _build_tooltip_html
from file_analyzer.meta_parser import FieldMeta


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_compute_quick_stats_includes_null_count_for_state() -> None:
    """Purpose: Dimension fields report SQL NULL count (zero when none present)."""

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
    stats = compute_quick_stats_parallel(meta, str(session.database_path), "data", 5, 2)
    session.close()
    assert stats["STATE"].null_count == 0


def test_build_tooltip_html_shows_null_zero_at_top() -> None:
    """Purpose: Tooltip HTML always lists NULL as the first stats row."""

    field = FieldMeta("STATE", "D", None, "State", "D")
    stats = FieldQuickStats(field=field, null_count=0, char_frequencies=[("MA", 100)])
    html = _build_tooltip_html(stats, 2)
    null_pos = html.index("NULL")
    ma_pos = html.index("MA")
    assert null_pos < ma_pos
    assert ">0<" in html.replace(",", "")


def test_build_tooltip_html_null_count_with_commas() -> None:
    field = FieldMeta("X", "Num", None, "x", "M")
    stats = FieldQuickStats(
        field=field,
        null_count=1234,
        numeric_summary={
            "min": 1.0,
            "max": 2.0,
            "sum": 3.0,
            "mean": 1.5,
            "median": 1.5,
        },
    )
    html = _build_tooltip_html(stats, 2)
    assert "NULL" in html
    assert "1,234" in html


def test_null_count_includes_sql_null_rows(tmp_path: Path) -> None:
    """Purpose: NULL row reflects actual SQL nulls in the column."""

    md = MetaDefinition(
        file_key_columns=["STATE"],
        fields=[FieldMeta("STATE", "D", None, "State", "D")],
    )
    db_path = tmp_path / "nulls.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE data AS
            SELECT * FROM (VALUES ('CA'), (NULL), ('NY')) AS t("STATE")
            """
        )
    finally:
        con.close()

    stats = compute_quick_stats_parallel(md, str(db_path), "data", 5, 1)
    assert stats["STATE"].null_count == 1
