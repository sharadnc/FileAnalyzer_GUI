"""Tests for Data Grid measure quick-filter range parsing."""

from __future__ import annotations

from file_analyzer.ui.grid_tab import _parse_measure_range_bounds


def test_parse_range_and_keyword() -> None:
    """``100 and 400`` yields inclusive ordered bounds."""

    assert _parse_measure_range_bounds("  100 and 400 ") == (100.0, 400.0)


def test_parse_range_hyphen_reversed() -> None:
    """Hyphen range swaps when the first bound is larger."""

    assert _parse_measure_range_bounds("400-100") == (100.0, 400.0)


def test_parse_range_to() -> None:
    """``to`` separator is accepted case-insensitively."""

    assert _parse_measure_range_bounds("1.5 TO 9") == (1.5, 9.0)


def test_parse_range_dotdot() -> None:
    """Two-dot range syntax is accepted."""

    assert _parse_measure_range_bounds("0..1") == (0.0, 1.0)


def test_parse_non_range_returns_none() -> None:
    """Plain text does not match range patterns."""

    assert _parse_measure_range_bounds("hello") is None
    assert _parse_measure_range_bounds("100") is None
