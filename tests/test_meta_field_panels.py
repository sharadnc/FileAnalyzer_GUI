"""Tests for FieldType panel rules (DISPLAY, YYYYMMDD)."""

from __future__ import annotations

from typing import Literal, cast

from file_analyzer.meta_parser import (
    FieldMeta,
    field_in_dimension_panels,
    field_in_measure_panels,
    field_quick_stats_use_yyyymmdd_values,
    is_display_only_field,
    is_yyyymmdd_field_type,
)

_FieldDType = Literal["D", "M"]


def _field(ft: str, dm: _FieldDType) -> FieldMeta:
    return FieldMeta(
        name="X",
        field_type=ft,
        field_length=None,
        description="",
        field_dtype=cast(_FieldDType, dm),
    )


def test_display_excluded_from_panels() -> None:
    """DISPLAY fields are not offered on dimension or measure shelves."""

    f = _field("DISPLAY", "D")
    assert is_display_only_field(f)
    assert not field_in_dimension_panels(f)
    assert not field_in_measure_panels(f)


def test_yyyymmdd_in_dimension_panels_even_when_datatype_m() -> None:
    """YYYYMMDD FieldType counts as dimension even if Datatype is M."""

    f = _field("YYYYMMDD", "M")
    assert is_yyyymmdd_field_type("yyyymmdd")
    assert field_in_dimension_panels(f)
    assert not field_in_measure_panels(f)


def test_yyyymmdd_spelling_variants() -> None:
    """Only ``YYYYMMDD`` matches; the old ``YYYYYMMDD`` typo does not."""

    assert is_yyyymmdd_field_type("YYYYMMDD")
    assert is_yyyymmdd_field_type("yyyymmdd")
    assert not is_yyyymmdd_field_type("YYYYYMMDD")


def test_field_quick_stats_use_yyyymmdd_for_date_storage_type() -> None:
    """Purpose: ``FieldType`` ``Date`` enables YYYYMMDD quick-stats formatting."""

    assert field_quick_stats_use_yyyymmdd_values(_field("Date", "D"))
    assert field_quick_stats_use_yyyymmdd_values(_field("Datetime", "M"))
