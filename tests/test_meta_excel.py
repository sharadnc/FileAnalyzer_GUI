"""Tests for Excel-backed metadata (:func:`parse_meta_from_excel`)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from file_analyzer.meta_parser import (
    format_excel_meta_columns_popup_message,
    missing_required_excel_meta_columns,
    parse_meta_from_excel,
    parse_meta_file,
    validate_meta_file_before_load,
)


def test_parse_meta_from_excel_reordered_columns(tmp_path: Path) -> None:
    """Purpose: headers in any order still map to FileKey and fields.

    Internal Logic
    ----------------
    Build a small DataFrame with permuted columns, write ``.xlsx``, parse, and assert.

    Example invocation
    ------------------
    ``pytest tests/test_meta_excel.py::test_parse_meta_from_excel_reordered_columns -q``
    """

    rows = [
        {
            "Field Description": "d1",
            "Primary Key": "Y",
            "Datatype": "D",
            "Field Name": "A",
            "Field Length": "",
            "FieldType": "Char",
        },
        {
            "Field Description": "m1",
            "Primary Key": "N",
            "Datatype": "M",
            "Field Name": "B",
            "Field Length": "10",
            "FieldType": "Num",
        },
        {
            "Field Description": "d2",
            "Primary Key": "Y",
            "Datatype": "D",
            "Field Name": "C",
            "Field Length": "",
            "FieldType": "Char",
        },
    ]
    df = pd.DataFrame(rows)
    df = df[["Field Description", "Primary Key", "Datatype", "Field Name", "Field Length", "FieldType"]]
    p = tmp_path / "meta.xlsx"
    df.to_excel(p, index=False, engine="openpyxl")
    md = parse_meta_from_excel(p)
    assert md.file_key_columns == ["A", "C"]
    assert [f.name for f in md.fields] == ["A", "B", "C"]
    assert md.fields_by_name["B"].field_dtype == "M"
    assert md.fields_by_name["B"].field_length == "10"


def test_parse_meta_file_dispatches_xlsx(tmp_path: Path) -> None:
    """Purpose: :func:`parse_meta_file` routes ``.xlsx`` to the Excel parser."""

    df = pd.DataFrame(
        [
            {
                "Field Name": "X",
                "Primary Key": "Y",
                "FieldType": "Char",
                "Datatype": "D",
                "Field Length": "",
                "Field Description": "pk",
            },
            {
                "Field Name": "Y",
                "Primary Key": "N",
                "FieldType": "Num",
                "Datatype": "M",
                "Field Length": "",
                "Field Description": "m",
            },
        ]
    )
    p = tmp_path / "m.xlsx"
    df.to_excel(p, index=False, engine="openpyxl")
    md = parse_meta_file(p)
    assert md.file_key_columns == ["X"]
    assert len(md.fields) == 2


def test_parse_meta_from_excel_date_datatypes_are_measures(tmp_path: Path) -> None:
    """Purpose: Excel ``Datatype`` cells ``Date`` / ``Datetime`` map to measure (``M``).

    Internal Logic
    ----------------
    Write two field rows with temporal datatype labels and assert ``field_dtype == \"M\"``.

    Example invocation
    --------------------
    ``pytest tests/test_meta_excel.py::test_parse_meta_from_excel_date_datatypes_are_measures -q``
    """

    df = pd.DataFrame(
        [
            {
                "Field Name": "DUE_DT",
                "Primary Key": "Y",
                "FieldType": "Date",
                "Datatype": "Date",
                "Field Length": "",
                "Field Description": "due",
            },
            {
                "Field Name": "TXN_DT",
                "Primary Key": "N",
                "FieldType": "Datetime",
                "Datatype": "Datetime",
                "Field Length": "",
                "Field Description": "txn",
            },
        ]
    )
    p = tmp_path / "dates.xlsx"
    df.to_excel(p, index=False, engine="openpyxl")
    md = parse_meta_from_excel(p)
    assert md.fields_by_name["DUE_DT"].field_dtype == "M"
    assert md.fields_by_name["TXN_DT"].field_dtype == "M"


def test_parse_meta_from_excel_string_varchar_datatypes_are_dimensions(tmp_path: Path) -> None:
    """Purpose: ``String`` / ``Varchar`` / ``Varchar2`` in Datatype map to dimension (``D``)."""

    df = pd.DataFrame(
        [
            {
                "Field Name": "A",
                "Primary Key": "Y",
                "FieldType": "Char",
                "Datatype": "String",
                "Field Length": "10",
                "Field Description": "s",
            },
            {
                "Field Name": "B",
                "Primary Key": "N",
                "FieldType": "Char",
                "Datatype": "Varchar",
                "Field Length": "20",
                "Field Description": "v",
            },
            {
                "Field Name": "C",
                "Primary Key": "N",
                "FieldType": "Char",
                "Datatype": "Varchar2",
                "Field Length": "30",
                "Field Description": "v2",
            },
        ]
    )
    p = tmp_path / "str.xlsx"
    df.to_excel(p, index=False, engine="openpyxl")
    md = parse_meta_from_excel(p)
    assert md.fields_by_name["A"].field_dtype == "D"
    assert md.fields_by_name["B"].field_dtype == "D"
    assert md.fields_by_name["C"].field_dtype == "D"


def test_missing_required_excel_columns_detected() -> None:
    """Purpose: incomplete header sets list the expected display labels."""

    missing = missing_required_excel_meta_columns(["Field Name", "Datatype"])
    assert "Primary Key" in missing
    assert "FieldType" in missing
    assert "Field Length" in missing
    assert "Field Description" in missing


def test_parse_meta_from_excel_requires_field_length_column(tmp_path: Path) -> None:
    """Purpose: workbooks without Field Length are rejected before row parsing."""

    df = pd.DataFrame(
        [
            {
                "Field Name": "A",
                "Primary Key": "Y",
                "FieldType": "Char",
                "Datatype": "D",
                "Field Description": "x",
            },
        ]
    )
    p = tmp_path / "incomplete.xlsx"
    df.to_excel(p, index=False, engine="openpyxl")
    try:
        parse_meta_from_excel(p)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Field Length" in str(exc)


def test_validate_meta_file_before_load_returns_message(tmp_path: Path) -> None:
    """Purpose: pre-load validation returns a user-facing warning string."""

    df = pd.DataFrame(columns=["Field Name", "Primary Key"])
    p = tmp_path / "bad.xlsx"
    df.to_excel(p, index=False, engine="openpyxl")
    msg = validate_meta_file_before_load(p)
    assert msg is not None
    assert "FieldType" in msg
    assert "Datatype" in msg
    assert "Field Length" in msg
