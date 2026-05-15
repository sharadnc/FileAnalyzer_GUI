"""Tests for chart-click human-readable filter summaries on the Data Grid."""

from __future__ import annotations

import json

from file_analyzer.ui.visualize_tab import build_chart_click_filter_human_summary


def test_bar_dimension_in_values() -> None:
    payload = json.loads('["United States"]')
    out = build_chart_click_filter_human_summary(
        "Bar",
        dims=["NAME"],
        measures=["POP"],
        customdata=payload,
    )
    assert out == "NAME in (United States)"


def test_stacked_bar_two_dimensions() -> None:
    out = build_chart_click_filter_human_summary(
        "Stacked Bar",
        dims=["NAME", "REGION"],
        measures=["POP"],
        customdata=["United States", "West"],
    )
    assert out == "NAME in (United States), REGION in (West)"


def test_histogram_measure_range() -> None:
    out = build_chart_click_filter_human_summary(
        "Histogram",
        dims=[],
        measures=["POP"],
        customdata=[2, 100.0, 200.0],
    )
    assert out == "POP in (100-200)"
