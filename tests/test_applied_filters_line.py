"""Tests for shared **Filters - …** summary formatting."""

from __future__ import annotations

from types import SimpleNamespace

from file_analyzer.ui.grid_tab import DataGridTab


def test_format_applied_filters_line_chart_and_panel() -> None:
    """Purpose: chart-linked and panel clauses join like the Data Grid pager."""

    tab = SimpleNamespace(
        _active_mode="chart",
        _chart_click_filter_where_sql="NAME = 'US'",
        _chart_click_filter_human_summary="NAME in (US)",
        _applied_panel_filter_summary="",
        _describe_live_column_filters_line=lambda: "",
    )
    tab._build_applied_filters_segments = (  # type: ignore[attr-defined]
        lambda: DataGridTab._build_applied_filters_segments(tab)  # type: ignore[arg-type]
    )
    line = DataGridTab._format_applied_filters_line(tab, loading=False)  # type: ignore[arg-type]
    assert line == "Filters - chart-linked selection (NAME in (US))"

    tab._active_mode = "user"  # type: ignore[attr-defined]
    tab._chart_click_filter_where_sql = ""  # type: ignore[attr-defined]
    tab._applied_panel_filter_summary = "STATE in (01,02)"  # type: ignore[attr-defined]
    line2 = DataGridTab._format_applied_filters_line(tab, loading=False)  # type: ignore[arg-type]
    assert line2 == "Filters - STATE in (01,02)"
