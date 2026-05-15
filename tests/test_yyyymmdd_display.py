"""Tests for :func:`format_yyyymmdd_display`."""

from __future__ import annotations

from datetime import date, datetime

from file_analyzer.meta_parser import FieldMeta, field_displays_as_yyyymmdd, format_yyyymmdd_display


def test_format_yyyymmdd_from_iso_string() -> None:
    assert format_yyyymmdd_display("2024-05-15") == "20240515"


def test_format_yyyymmdd_from_compact_numeric() -> None:
    assert format_yyyymmdd_display(20240515) == "20240515"
    assert format_yyyymmdd_display(20240515.0) == "20240515"


def test_format_yyyymmdd_from_datetime() -> None:
    assert format_yyyymmdd_display(datetime(2024, 5, 15, 12, 30)) == "20240515"
    assert format_yyyymmdd_display(date(2024, 5, 15)) == "20240515"


def test_field_displays_as_yyyymmdd_flag() -> None:
    f = FieldMeta(
        name="D",
        field_type="YYYYMMDD",
        field_length=None,
        description="",
        field_dtype="M",
    )
    assert field_displays_as_yyyymmdd(f)
