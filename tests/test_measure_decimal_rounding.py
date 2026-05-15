"""Tests for measure quick-stats decimal rounding."""

from __future__ import annotations

from file_analyzer.meta_parser import FieldMeta
from file_analyzer.stats_service import FieldQuickStats, apply_measure_decimal_rounding_to_quick_stats


def test_apply_measure_decimal_rounding_to_quick_stats_rounds_m_measures() -> None:
    """Measure (``M``) numeric summaries are rounded; dimensions are untouched."""

    m_field = FieldMeta(
        name="POP",
        field_type="Num",
        field_length=None,
        description="population",
        field_dtype="M",
    )
    m_stats = FieldQuickStats(
        field=m_field,
        numeric_summary={
            "min": 1.23456,
            "max": 9.87654,
            "sum": 11.11111,
            "mean": 5.55555,
            "median": 3.33333,
        },
    )
    d_field = FieldMeta(
        name="STATE",
        field_type="Char",
        field_length="2",
        description="state",
        field_dtype="D",
    )
    d_stats = FieldQuickStats(field=d_field, char_frequencies=[("CA", 10)])

    out = apply_measure_decimal_rounding_to_quick_stats(
        {"POP": m_stats, "STATE": d_stats},
        decimal_places=2,
    )
    assert out["STATE"] is d_stats
    assert out["POP"].numeric_summary is not None
    assert out["POP"].numeric_summary["min"] == 1.23
    assert out["POP"].numeric_summary["max"] == 9.88


def test_apply_measure_decimal_rounding_preserves_nan() -> None:
    """NaN entries in numeric summaries stay NaN after rounding."""

    m_field = FieldMeta(
        name="X",
        field_type="Num",
        field_length=None,
        description="x",
        field_dtype="M",
    )
    st = FieldQuickStats(
        field=m_field,
        numeric_summary={
            "min": float("nan"),
            "max": 1.0,
            "sum": 0.0,
            "mean": 0.0,
            "median": 0.0,
        },
    )
    out = apply_measure_decimal_rounding_to_quick_stats({"X": st}, 3)
    assert out["X"].numeric_summary is not None
    v = out["X"].numeric_summary["min"]
    assert v != v
