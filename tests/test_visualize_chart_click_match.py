"""Tests for chart-click → chart-table dimension filtering (pure logic + payloads)."""

from __future__ import annotations

import json

from file_analyzer.ui.visualize_tab import (
    _chart_dimension_click_equals,
    _unwrap_plotly_click_value,
    filter_chart_rows_for_plotly_click,
)


def test_click_equals_numeric_coercion() -> None:
    assert _chart_dimension_click_equals(1, "1") is True
    assert _chart_dimension_click_equals(1.0, 1) is True


def test_click_equals_string_casefold() -> None:
    assert _chart_dimension_click_equals("United States", "united states") is True
    assert _chart_dimension_click_equals("  US  ", "us") is True


def test_unwrap_nested_single_lists() -> None:
    assert _unwrap_plotly_click_value([["United States"]]) == "United States"
    assert _unwrap_plotly_click_value(["x"]) == "x"


def test_filter_bar_name_dimension_json_array_string() -> None:
    """Simulate JS ``JSON.stringify([\"United States\"])`` payload after ``json.loads``."""

    rows: list[list[object]] = [
        ["United States", 331.0],
        ["Canada", 40.0],
        ["Mexico", 130.0],
    ]
    payload = json.loads('["United States"]')
    out = filter_chart_rows_for_plotly_click("Bar", ["NAME", "POP"], ["NAME"], rows, payload)
    assert len(out) == 1
    assert out[0][0] == "United States"


def test_filter_bar_nested_customdata_like_plotly() -> None:
    """Some Plotly paths emit an extra list wrapper around the scalar."""

    rows = [["United States", 1], ["Canada", 2]]
    payload = [["United States"]]
    out = filter_chart_rows_for_plotly_click("Bar", ["NAME", "POP"], ["NAME"], rows, payload)
    assert len(out) == 1


def test_filter_bar_resolves_name_column_when_not_first_logical_bug() -> None:
    """If columns were ever reordered, ``ctx_dims`` still picks ``NAME`` — here index 1."""

    rows = [
        [1, "United States", 10.0],
        [2, "Canada", 20.0],
    ]
    payload = ["United States"]
    out = filter_chart_rows_for_plotly_click("Bar", ["ID", "NAME", "POP"], ["NAME"], rows, payload)
    assert len(out) == 1
    assert out[0][1] == "United States"


def test_filter_bar_json_string_payload() -> None:
    """Bridge may deliver a JSON string (not an array) for a single category."""

    rows = [["United States", 1]]
    payload = json.loads('"United States"')
    out = filter_chart_rows_for_plotly_click("Bar", ["NAME", "POP"], ["NAME"], rows, payload)
    assert len(out) == 1


def test_filter_click_subset_then_quick_filter_source_simulation() -> None:
    """After a click, quick-filters must scan the subset (see ``_chart_filter_source_rows``).

    Purpose
    -------
    Regression: previously ``_apply_column_filters`` always read
    ``_chart_original_rows``, undoing the click filter. This test documents the
    expected subset size; the UI fix keeps ``_chart_filter_source_rows`` in sync
    with the last rebuild input.
    """

    full: list[list[object]] = [
        ["United States", 100.0],
        ["Canada", 200.0],
    ]
    subset = filter_chart_rows_for_plotly_click("Bar", ["NAME", "POP"], ["NAME"], full, ["United States"])
    assert len(subset) == 1
    # Simulate typing "100" in POP filter: only US row matches within subset.
    filtered: list[list[object]] = []
    for row in subset:
        if "100" in str(row[1]):
            filtered.append(row)
    assert len(filtered) == 1
