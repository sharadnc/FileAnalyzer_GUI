"""Unit tests for the File Analyzer metadata parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_analyzer.meta_parser import FieldMeta, MetaDefinition, parse_field_def, parse_meta_file, parse_meta_line


def _repo_root() -> Path:
    """Return the repository root path (directory containing ``pyproject.toml``).

    Purpose
    -------
    Keep tests independent of the current working directory by locating files
    via absolute paths computed from this test module location.

    Returns
    -------
    Path
        Repository root path.
    """

    return Path(__file__).resolve().parents[1]


def test_parse_field_def_empty_length() -> None:
    """FieldLength should be parsed as ``None`` when it is empty (``||``)."""

    field = parse_field_def("ESTIMATESBASE2020|Num||Base|M")
    assert field.name == "ESTIMATESBASE2020"
    assert field.field_type == "Num"
    assert field.field_length is None
    assert field.description == "Base"
    assert field.field_dtype == "M"


def test_parse_meta_file_sample_nst_meta() -> None:
    """The parser should correctly infer FileKey columns and ordered fields."""

    meta_path = _repo_root() / "sample" / "NST-EST2025-ALLDATA.csv_Meta"
    md = parse_meta_file(meta_path)

    assert md.file_key_columns == ["SUMLEV", "REGION", "DIVISION"]
    assert len(md.fields) == 12

    # Preserve order for UI lists and default chart dropdowns.
    assert [f.name for f in md.fields[:5]] == ["SUMLEV", "REGION", "DIVISION", "STATE", "NAME"]

    # Validate one dimension and one measure.
    assert md.fields_by_name["STATE"].field_dtype == "D"
    assert md.fields_by_name["ESTIMATESBASE2020"].field_dtype == "M"
    assert md.fields_by_name["ESTIMATESBASE2020"].field_length is None


def test_parse_meta_line_matches_parse_meta_file() -> None:
    """Parsing a raw meta line should match parsing from file."""

    meta_path = _repo_root() / "sample" / "NST-EST2025-ALLDATA.csv_Meta"
    meta_line = meta_path.read_text(encoding="utf-8").strip()

    md_from_line = parse_meta_line(meta_line)
    md_from_file = parse_meta_file(meta_path)

    assert md_from_line == md_from_file

