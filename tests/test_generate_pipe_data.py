"""Tests for :mod:`tools.generate_pipe_data`."""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd
import pytest

# tools/ is not installed as a package; import by path.
import sys

_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from generate_pipe_data import (  # noqa: E402
    ColumnSpec,
    RuleValueGenerator,
    _generate_unique_number,
    generate_record,
    read_generation_metadata,
    write_pipe_delimited_file,
)


def test_read_generation_metadata_loanpop_template() -> None:
    """Purpose: LoanPop.xlsx loads with Rules and expected column count."""

    root = Path(__file__).resolve().parents[1]
    meta = root / "templates" / "LoanPop.xlsx"
    if not meta.is_file():
        pytest.skip(f"Template not present: {meta}")
    specs = read_generation_metadata(meta)
    assert len(specs) == 15
    assert specs[0].name == "LOAN_NBR"
    assert "Unique" in specs[0].rules


def test_unique_number_zero_padded_digits_only() -> None:
    """Purpose: Unique Number values are digits only, no column-name prefix."""

    issued: dict[str, set[str]] = {}
    spec_narrow = ColumnSpec("LOAN_NBR", "Char", 3, "Unique Number")
    spec_wide = ColumnSpec("BORROWER_ID", "Char", 50, "Unique Number")

    assert _generate_unique_number(spec_narrow, 6, issued) == "007"
    wide_val = _generate_unique_number(spec_wide, 0, issued)
    assert len(wide_val) == 50
    assert wide_val.isdigit()
    assert "BORROWER" not in wide_val.upper()
    assert wide_val == "1".zfill(50)


def test_datetime_columns_use_datetime_format() -> None:
    """Purpose: Datatype Datetime emits ``YYYY-MM-DD HH:MM:SS``."""

    specs = [
        ColumnSpec("TXN_DT", "Datetime", None, "Any datetime in the month"),
        ColumnSpec(
            "DT_SRCE_BEG",
            "Datetime",
            None,
            "Any datetime in the month < TXN_DT",
        ),
        ColumnSpec("DT_SRCE_END", "Datetime", None, "9999-12-31 00:00:00"),
        ColumnSpec("DUE_DT", "Date", None, "Last Date of the Month"),
    ]
    gen = RuleValueGenerator(random.Random(42))
    row = generate_record(specs, 0, gen)
    txn_dt, src_beg, src_end, due_dt = row

    assert len(txn_dt) == 19 and txn_dt[10] == " "
    assert txn_dt[:10].count("-") == 2 and txn_dt[11:].count(":") == 2
    assert len(src_beg) == 19
    assert src_end == "9999-12-31 00:00:00"
    assert len(due_dt) == 10 and " " not in due_dt
    assert _parse_txn_before_src(txn_dt, src_beg)


def _parse_txn_before_src(txn: str, src: str) -> bool:
    from datetime import datetime

    fmt = "%Y-%m-%d %H:%M:%S"
    return datetime.strptime(src, fmt) < datetime.strptime(txn, fmt)


def test_generate_record_respects_cross_field_cap(tmp_path: Path) -> None:
    """Purpose: numeric rule ``<OtherField`` caps values using row context."""

    rows = [
        {
            "Field Name": "LOAN_AMT",
            "Primary Key": "N",
            "FieldType": "M",
            "Datatype": "Num",
            "Field Length": "",
            "Field Description": "loan",
            "Rules": ">100 and <10000",
        },
        {
            "Field Name": "UPB",
            "Primary Key": "N",
            "FieldType": "M",
            "Datatype": "Num",
            "Field Length": "",
            "Field Description": "upb",
            "Rules": ">100 and <10000 and <LOAN_AMT",
        },
    ]
    meta = tmp_path / "meta.xlsx"
    pd.DataFrame(rows).to_excel(meta, index=False, engine="openpyxl")
    specs = read_generation_metadata(meta)
    gen = RuleValueGenerator(random.Random(99))
    values = generate_record(specs, 0, gen)
    loan_amt = float(values[0])
    upb = float(values[1])
    assert 100 < loan_amt < 10000
    assert 100 < upb < loan_amt


def test_write_pipe_delimited_file_header_and_rows(tmp_path: Path) -> None:
    """Purpose: output file has header plus N delimiter-separated rows."""

    rows = [
        {
            "Field Name": "ID",
            "Primary Key": "Y",
            "FieldType": "D",
            "Datatype": "Char",
            "Field Length": 5,
            "Field Description": "id",
            "Rules": "Unique Number",
        },
        {
            "Field Name": "FLAG",
            "Primary Key": "N",
            "FieldType": "D",
            "Datatype": "Char",
            "Field Length": 1,
            "Field Description": "flag",
            "Rules": "A",
        },
    ]
    meta = tmp_path / "tiny.xlsx"
    out = tmp_path / "tiny.pipe"
    pd.DataFrame(rows).to_excel(meta, index=False, engine="openpyxl")
    specs = read_generation_metadata(meta)
    write_pipe_delimited_file(specs, 3, out, rng=random.Random(1))
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "ID|FLAG"
    assert len(lines) == 4
    assert all(line.count("|") == 1 for line in lines[1:])
