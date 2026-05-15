"""Tests for pie chart label/layout heuristics."""

from __future__ import annotations

from file_analyzer.ui.visualize_tab import _pie_chart_display_options


def test_pie_few_slices_shows_outside_labels() -> None:
    trace, layout = _pie_chart_display_options(["A", "B", "C"], [30, 40, 30])
    assert trace["textinfo"] == "label+percent"
    assert trace["textposition"] == "outside"


def test_pie_many_slices_hides_labels_uses_legend() -> None:
    labels = [f"S{i}" for i in range(20)]
    values = [1.0] * 20
    trace, layout = _pie_chart_display_options(labels, values)
    assert trace["textinfo"] == "none"
    assert layout["showlegend"] is True


def test_pie_tight_margins_for_large_chart() -> None:
    _, layout = _pie_chart_display_options(["X"], [100])
    margin = layout["margin"]
    assert isinstance(margin, dict)
    assert margin.get("l", 99) <= 10
