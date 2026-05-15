"""Tests that pivot field lists can use the same quick-stats HTML as Visualize."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_analyzer.meta_parser import FieldMeta, MetaDefinition
from file_analyzer.stats_service import FieldQuickStats
from file_analyzer.ui.models import LoadedDatasetContext
from file_analyzer.ui.quick_stats_tooltips import quick_stats_tooltips_by_field_name


def test_quick_stats_tooltips_by_field_name_includes_state_and_loan_amt() -> None:
    """Purpose: Pivot Rows/Values lists get tooltips for dimensions and measures."""

    state = FieldMeta("STATE", "D", None, "State", "D")
    loan = FieldMeta(
        "LOAN_AMT",
        "M",
        "12",
        "Loan Amount",
        "M",
    )
    meta = MetaDefinition(file_key_columns=["STATE"], fields=[state, loan])
    ctx = LoadedDatasetContext(
        meta=meta,
        database_path=Path("_missing_pivot_tooltip_test.duckdb"),
        temp_dir=Path("."),
        quick_stats={
            "STATE": FieldQuickStats(field=state, null_count=0, char_frequencies=[("MA", 5)]),
            "LOAN_AMT": FieldQuickStats(
                field=loan,
                null_count=0,
                numeric_summary={
                    "min": 1.0,
                    "max": 2.0,
                    "sum": 3.0,
                    "mean": 1.5,
                    "median": 1.5,
                },
            ),
        },
        source_data_path=Path("x.txt"),
        source_delimiter="|",
        table_name="data",
        measure_decimal_places=2,
    )
    tips = quick_stats_tooltips_by_field_name(ctx)
    assert "NULL" in tips["STATE"]
    assert ">0<" in tips["STATE"].replace(",", "")
    assert "Min" in tips["LOAN_AMT"]
