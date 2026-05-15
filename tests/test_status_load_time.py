"""Tests for status bar load-time formatting."""

from __future__ import annotations

from file_analyzer.ui.status_zoom import format_load_duration_hms, format_status_bar_loading_time


def test_format_load_duration_hms_zero() -> None:
    assert format_load_duration_hms(0) == "00:00:00"


def test_format_load_duration_hms_minutes_seconds() -> None:
    assert format_load_duration_hms(125.4) == "00:02:05"


def test_format_load_duration_hms_hours() -> None:
    assert format_load_duration_hms(3723) == "01:02:03"


def test_format_status_bar_loading_time_completed() -> None:
    assert format_status_bar_loading_time(elapsed_seconds=125.4) == "Loading time - 00:02:05"


def test_format_status_bar_loading_time_idle() -> None:
    assert format_status_bar_loading_time() == "Loading time - --:--:--"


def test_format_status_bar_loading_time_in_progress() -> None:
    assert format_status_bar_loading_time(loading=True) == "Loading time - …"
