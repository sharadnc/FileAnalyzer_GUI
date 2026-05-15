"""Unit tests for chart-table measure filter numeric coercion."""

from __future__ import annotations

from file_analyzer.ui.visualize_tab import _coerce_numeric_cell_for_chart_filter


def test_coerce_plain_numbers() -> None:
    """Int and float cells map to finite floats."""

    assert _coerce_numeric_cell_for_chart_filter(42) == 42.0
    assert _coerce_numeric_cell_for_chart_filter(3.14) == 3.14


def test_coerce_string_with_commas() -> None:
    """Thousands separators in string cells are stripped before parsing."""

    assert _coerce_numeric_cell_for_chart_filter("1,234.5") == 1234.5


def test_coerce_rejects_invalid_and_bool() -> None:
    """Invalid text, None, and bool do not produce numeric filter values."""

    assert _coerce_numeric_cell_for_chart_filter("x") is None
    assert _coerce_numeric_cell_for_chart_filter(None) is None
    assert _coerce_numeric_cell_for_chart_filter(True) is None
