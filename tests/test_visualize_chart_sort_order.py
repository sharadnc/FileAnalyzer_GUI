"""Tests for chart-table sort order composition on the Visualize tab."""

from __future__ import annotations

from file_analyzer.ui.visualize_tab import _build_chart_sort_order


def test_pk_columns_before_remaining_chart_columns() -> None:
    """Primary keys in meta order precede other chart columns, all ascending."""

    order = _build_chart_sort_order([], ["SUMLEV", "NAME", "POP"], ["SUMLEV", "NAME"])
    assert order == [("SUMLEV", True), ("NAME", True), ("POP", True)]


def test_user_sort_precedes_pk_and_rest() -> None:
    """Explicit user keys stay first; PKs and other columns follow."""

    order = _build_chart_sort_order([("POP", False)], ["NAME", "POP"], ["NAME"])
    assert order[0] == ("POP", False)
    assert "NAME" in [c for c, _ in order[1:]]


def test_user_column_skipped_if_not_in_chart() -> None:
    """Unknown user sort keys are ignored."""

    order = _build_chart_sort_order([("ZZZ", True)], ["A", "B"], [])
    assert order == [("A", True), ("B", True)]
