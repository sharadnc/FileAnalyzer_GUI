"""Generate a pipe-delimited data file from Excel column metadata and Rules.

Purpose
-------
Read spreadsheet metadata (field names, datatypes, lengths, and **Rules** cells)
and emit a fictitious ``|``-delimited dataset with *N* rows for testing File
Analyzer or downstream pipelines.

Internal Logic
----------------
1. Load the first worksheet with ``pandas.read_excel``.
2. Build ordered :class:`ColumnSpec` rows (name, Char/Num/Date, length, rules).
3. For each record index, walk columns in sheet order and compute a cell value
   from parsed rules (enums, numeric bounds, cross-field ``<OtherField``, dates,
   US state/county, unique keys).
4. Write a header row plus *N* body rows using the pipe delimiter.

Example invocation
--------------------
From the repository root::

    py -3 tools/generate_pipe_data.py
    py -3 tools/generate_pipe_data.py --records 5000 --output sample/LoanPop_sample.txt
    py -3 tools/generate_pipe_data.py --meta-path templates/LoanPop.xlsx --records 100
"""

from __future__ import annotations

import argparse
import calendar
import logging
import random
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# US states (abbrev + name) for "US states only" rules.
_US_STATES: Tuple[str, ...] = (
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
)

# Sample counties per state for dependent county generation.
_COUNTIES_BY_STATE: Mapping[str, Tuple[str, ...]] = {
    "CA": ("Los Angeles", "San Diego", "Orange", "Santa Clara", "Alameda"),
    "TX": ("Harris", "Dallas", "Tarrant", "Bexar", "Travis"),
    "NY": ("Kings", "Queens", "New York", "Suffolk", "Bronx"),
    "FL": ("Miami-Dade", "Broward", "Palm Beach", "Hillsborough", "Orange"),
    "IL": ("Cook", "DuPage", "Lake", "Will", "Kane"),
    "PA": ("Philadelphia", "Allegheny", "Montgomery", "Bucks", "Delaware"),
    "OH": ("Cuyahoga", "Franklin", "Hamilton", "Summit", "Montgomery"),
    "GA": ("Fulton", "Gwinnett", "Cobb", "DeKalb", "Chatham"),
    "NC": ("Wake", "Mecklenburg", "Guilford", "Forsyth", "Cumberland"),
    "MI": ("Wayne", "Oakland", "Macomb", "Kent", "Genesee"),
}

_DEFAULT_COUNTIES: Tuple[str, ...] = ("Central", "North", "South", "East", "West")

_NUMERIC_BOUNDS_RE = re.compile(
    r"(?P<lo_op>>|>=)\s*(?P<lo_val>-?\d+(?:\.\d+)?)"
    r"(?:\s+and\s+(?P<hi_op><|<=)\s*(?P<hi_val>-?\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)
_CROSS_FIELD_LT_RE = re.compile(r"<\s*([A-Za-z0-9_]+)", re.IGNORECASE)
_ENUM_SPLIT_RE = re.compile(r"\s*,\s*")

# Canonical output formats for Date vs Datetime columns.
_DATE_FMT = "%Y-%m-%d"
_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class ColumnSpec:
    """One column definition row from the metadata workbook.

    Purpose
    -------
    Hold everything needed to synthesize values for a single output column.

    Internal Logic
    ----------------
    Immutable record built by :func:`read_generation_metadata` from Excel cells.

    Example invocation
    ------------------
    ``ColumnSpec(name="LOAN_NBR", data_type="Char", field_length=3, rules="Unique Number")``
    """

    name: str
    data_type: str
    field_length: Optional[int]
    rules: str
    is_primary_key: bool = False


def _repo_root() -> Path:
    """Return the repository root (parent of ``tools/``).

    Purpose
    -------
    Resolve default paths for templates and output relative to the project.

    Example invocation
    ------------------
    ``root = _repo_root(); meta = root / "templates" / "LoanPop.xlsx"``
    """

    return Path(__file__).resolve().parents[1]


def _normalize_header(value: object) -> str:
    """Normalize a spreadsheet header to alphanumeric lowercase.

    Purpose
    -------
    Match columns regardless of spacing or casing (e.g. ``Field Name``).

    Example invocation
    ------------------
    ``assert _normalize_header("Field Length") == "fieldlength"``
    """

    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


def _truthy_primary_key(value: object) -> bool:
    """Return True when the Primary Key cell marks a FileKey column.

    Purpose
    -------
    Mirror File Analyzer meta conventions (``Y``, ``Yes``, ``1``, …).

    Example invocation
    ------------------
    ``assert _truthy_primary_key("Y") is True``
    """

    if value is None:
        return False
    try:
        import pandas as pd

        if pd.isna(value):
            return False
    except Exception:
        pass
    token = str(value).strip().upper()
    return token in ("Y", "YES", "TRUE", "1", "T")


def _optional_int_length(value: object) -> Optional[int]:
    """Parse **Field Length** as a positive integer or ``None`` when blank.

    Purpose
    -------
    Excel often stores lengths as floats (e.g. ``3.0``).

    Example invocation
    ------------------
    ``assert _optional_int_length(50.0) == 50``
    """

    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        as_float = float(value)
        if as_float <= 0:
            return None
        return int(as_float) if as_float == int(as_float) else int(round(as_float))
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = int(float(text))
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def read_generation_metadata(meta_path: Path) -> List[ColumnSpec]:
    """Load column specs including **Rules** from an Excel metadata workbook.

    Purpose
    -------
    Produce ordered :class:`ColumnSpec` list for data generation (separate from
    File Analyzer's D/M ``meta_parser`` layout).

    Internal Logic
    ----------------
    1. ``pandas.read_excel`` on the first sheet.
    2. Map headers: Field Name, Datatype (Char/Num/Date), Field Length, Rules,
       Primary Key.
    3. Skip blank field names; coerce length and rules text.

    Parameters
    ----------
    meta_path:
        Path to ``.xlsx`` metadata (e.g. ``templates/LoanPop.xlsx``).

    Returns
    -------
    List[ColumnSpec]
        Non-empty field rows in sheet order.

    Raises
    ------
    FileNotFoundError
        If ``meta_path`` does not exist.
    ValueError
        If required columns are missing.

    Example invocation
    ------------------
    ``specs = read_generation_metadata(Path("templates/LoanPop.xlsx"))``
    """

    if not meta_path.exists():
        raise FileNotFoundError(f"Metadata workbook not found: {meta_path}")

    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("Reading Excel metadata requires pandas.") from exc

    try:
        df = pd.read_excel(meta_path, engine="openpyxl")
    except Exception as exc:
        raise ValueError(f"Failed to read Excel metadata from {meta_path}: {exc}") from exc

    if df.empty:
        raise ValueError(f"Excel metadata sheet is empty: {meta_path}")

    slot_to_col: Dict[str, str] = {}
    for col in df.columns:
        norm = _normalize_header(col)
        if norm in ("fieldname", "columnname", "name") and "field_name" not in slot_to_col:
            slot_to_col["field_name"] = str(col)
        elif norm in ("datatype", "type") and "data_type" not in slot_to_col:
            slot_to_col["data_type"] = str(col)
        elif norm in ("fieldlength", "length") and "field_length" not in slot_to_col:
            slot_to_col["field_length"] = str(col)
        elif norm == "rules" and "rules" not in slot_to_col:
            slot_to_col["rules"] = str(col)
        elif norm in ("primarykey", "pk") and "primary_key" not in slot_to_col:
            slot_to_col["primary_key"] = str(col)

    required = ("field_name", "data_type", "rules")
    missing = [s for s in required if s not in slot_to_col]
    if missing:
        raise ValueError(
            f"Excel metadata missing columns {missing}. Found: {list(df.columns)}"
        )

    specs: List[ColumnSpec] = []
    for _, row in df.iterrows():
        raw_name = row.get(slot_to_col["field_name"])
        if raw_name is None:
            continue
        try:
            import pandas as pd

            if pd.isna(raw_name):
                continue
        except Exception:
            pass
        name = str(raw_name).strip()
        if not name or name.lower() == "nan":
            continue

        raw_type = row.get(slot_to_col["data_type"], "Char")
        data_type = "Char" if raw_type is None else str(raw_type).strip()
        if not data_type:
            data_type = "Char"

        rules_cell = row.get(slot_to_col["rules"], "")
        rules = "" if rules_cell is None else str(rules_cell).strip()
        try:
            import pandas as pd

            if pd.isna(rules_cell):
                rules = ""
        except Exception:
            pass

        fl_col = slot_to_col.get("field_length")
        field_length = _optional_int_length(row.get(fl_col)) if fl_col else None

        pk_col = slot_to_col.get("primary_key")
        is_pk = _truthy_primary_key(row.get(pk_col)) if pk_col else False

        specs.append(
            ColumnSpec(
                name=name,
                data_type=data_type,
                field_length=field_length,
                rules=rules,
                is_primary_key=is_pk,
            )
        )

    if not specs:
        raise ValueError(f"No field rows found in {meta_path}")

    return specs


def _apply_char_length(value: str, field_length: Optional[int]) -> str:
    """Truncate a string to **Field Length** when specified.

    Purpose
    -------
    Honor metadata width for character columns.

    Example invocation
    ------------------
    ``assert _apply_char_length("ABCDEF", 3) == "ABC"``
    """

    if field_length is None or field_length <= 0:
        return value
    return value[:field_length]


def _context_lookup(context: Mapping[str, str], field_ref: str) -> Optional[float]:
    """Resolve a cross-field reference to a numeric value (case-insensitive).

    Purpose
    -------
    Support rules like ``<Loan_amt`` against ``LOAN_AMT`` in the same row.

    Example invocation
    ------------------
    ``_context_lookup({"LOAN_AMT": "500000"}, "loan_amt")`` → ``500000.0``
    """

    ref = field_ref.strip().upper()
    for key, raw in context.items():
        if key.upper() == ref:
            try:
                return float(str(raw).replace(",", ""))
            except ValueError:
                return None
    return None


def _parse_numeric_rule(
    rules: str, context: Mapping[str, str]
) -> Tuple[Optional[float], Optional[float], List[str]]:
    """Extract lower/upper bounds and cross-field ``<Field`` references from rules.

    Purpose
    -------
    Parse patterns such as ``>300000 and <1000000000 and <Loan_amt``.

    Returns
    -------
    Tuple[Optional[float], Optional[float], List[str]]
        ``(low_exclusive_or_none, high_exclusive_or_none, cross_field_names)``
    """

    low: Optional[float] = None
    high: Optional[float] = None
    cross_fields: List[str] = []

    for match in _NUMERIC_BOUNDS_RE.finditer(rules):
        lo_val = float(match.group("lo_val"))
        lo_op = match.group("lo_op")
        low = lo_val if lo_op == ">" else lo_val

        hi_val = match.group("hi_val")
        if hi_val is not None:
            hi_op = match.group("hi_op")
            hi_f = float(hi_val)
            high = hi_f if hi_op == "<" else hi_f

    for cref in _CROSS_FIELD_LT_RE.findall(rules):
        if cref.upper() not in {c.upper() for c in cross_fields}:
            cross_fields.append(cref)

    # If cross-field caps exist, tighten the high bound from context.
    for cref in cross_fields:
        cap = _context_lookup(context, cref)
        if cap is not None:
            high = cap if high is None else min(high, cap)

    return low, high, cross_fields


def _random_numeric(
    rng: random.Random,
    low: Optional[float],
    high: Optional[float],
    *,
    as_int: bool = True,
) -> str:
    """Pick a random number within open bounds (defaults applied when missing).

    Purpose
    -------
    Synthesize measure columns with rule-derived min/max.

    Example invocation
    ------------------
    ``_random_numeric(random.Random(1), 300000, 1_000_000)`` → ``"742831"``
    """

    lo = low if low is not None else 0.0
    hi = high if high is not None else lo + 1_000_000.0
    if hi <= lo:
        hi = lo + 1.0
    # Stay strictly inside open interval when bounds came from ``>`` / ``<``.
    span = hi - lo
    value = lo + rng.random() * span
    if as_int:
        # Keep a margin below the upper cap for strict ``<`` semantics.
        upper = hi - 1.0 if hi > lo + 2 else hi
        value = lo + 1.0 + rng.random() * max(upper - lo - 1.0, 1.0)
        return str(int(round(value)))
    return f"{value:.2f}"


def _parse_cell_datetime(value: str) -> Optional[datetime]:
    """Parse a context cell as :class:`datetime` (date-only values become midnight).

    Purpose
    -------
    Support cross-field rules such as ``< TXN_DT`` when comparing datetimes.

    Example invocation
    ------------------
    ``_parse_cell_datetime("2022-09-11 14:30:00")`` → datetime(...)
    """

    text = value.strip()
    if not text:
        return None
    for fmt in (_DATETIME_FMT, _DATE_FMT):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _context_datetime(
    context: Mapping[str, str],
    field_ref: str,
) -> Optional[datetime]:
    """Look up another column's value in ``context`` and parse as datetime.

    Purpose
    -------
    Resolve ``TXN_DT`` references case-insensitively for datetime cap rules.

    Example invocation
    ------------------
    ``_context_datetime({"TXN_DT": "2022-09-11 08:00:00"}, "txn_dt")``
    """

    ref = field_ref.strip().upper()
    for key, raw in context.items():
        if key.upper() == ref:
            return _parse_cell_datetime(str(raw))
    return None


def _random_date_in_month(rng: random.Random, year: int, month: int) -> str:
    """Return ``YYYY-MM-DD`` for a random day in the given month.

    Purpose
    -------
    Implement **Any date in the month** rules.

    Example invocation
    ------------------
    ``_random_date_in_month(random.Random(0), 2024, 6)`` → ``"2024-06-17"``
    """

    last_day = calendar.monthrange(year, month)[1]
    day = rng.randint(1, last_day)
    return date(year, month, day).strftime(_DATE_FMT)


def _random_datetime_in_month(rng: random.Random, year: int, month: int) -> str:
    """Return ``YYYY-MM-DD HH:MM:SS`` for a random moment in the given month.

    Purpose
    -------
    Implement **Any datetime in the month** rules for Datetime columns.

    Example invocation
    ------------------
    ``_random_datetime_in_month(random.Random(0), 2024, 6)`` → ``"2024-06-17 14:22:05"``
    """

    last_day = calendar.monthrange(year, month)[1]
    day = rng.randint(1, last_day)
    hour = rng.randint(0, 23)
    minute = rng.randint(0, 59)
    second = rng.randint(0, 59)
    return datetime(year, month, day, hour, minute, second).strftime(_DATETIME_FMT)


def _random_datetime_in_month_before(
    rng: random.Random,
    year: int,
    month: int,
    before: datetime,
) -> str:
    """Return a random datetime in ``year``/``month`` strictly before ``before``.

    Purpose
    -------
    Implement rules like **Any datetime in the month < TXN_DT**.

    Example invocation
    ------------------
    ``_random_datetime_in_month_before(rng, 2022, 9, datetime(2022, 9, 15, 12, 0, 0))``
    """

    month_start = datetime(year, month, 1, 0, 0, 0)
    cap = before - timedelta(seconds=1)
    if cap < month_start:
        return month_start.strftime(_DATETIME_FMT)
    span_seconds = (cap - month_start).total_seconds()
    offset = int(rng.random() * span_seconds)
    chosen = month_start + timedelta(seconds=offset)
    return chosen.strftime(_DATETIME_FMT)


def _last_date_of_month(rng: random.Random) -> str:
    """Return the last calendar day of a random recent month.

    Purpose
    -------
    Implement **Last Date of the Month** rules.

    Example invocation
    ------------------
    ``_last_date_of_month(random.Random(2))`` → ``"2025-01-31"``
    """

    year = rng.randint(2018, 2026)
    month = rng.randint(1, 12)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day).strftime(_DATE_FMT)


def _last_datetime_of_month(rng: random.Random) -> str:
    """Return the last calendar day of a random month at ``23:59:59``.

    Purpose
    -------
    Datetime fallback when rules mention last day of month on a Datetime column.

    Example invocation
    ------------------
    ``_last_datetime_of_month(random.Random(1))`` → ``"2024-06-30 23:59:59"``
    """

    year = rng.randint(2018, 2026)
    month = rng.randint(1, 12)
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, last_day, 23, 59, 59).strftime(_DATETIME_FMT)


def _generate_date_value(
    rng: random.Random,
    rules: str,
    rules_lower: str,
    month_anchor: Optional[Tuple[int, int]],
) -> Tuple[str, Tuple[int, int]]:
    """Produce a **Date** column value (``YYYY-MM-DD`` only).

    Purpose
    -------
    Keep Date and Datetime formatting separate per metadata **Datatype**.

    Returns
    -------
    Tuple[str, Tuple[int, int]]
        Formatted value and the month anchor used or established.

    Example invocation
    ------------------
    ``val, anchor = _generate_date_value(rng, "", "last date of the month", None)``
    """

    if "9999-12-31" in rules and "00:00:00" not in rules:
        return "9999-12-31", month_anchor or (rng.randint(2018, 2026), rng.randint(1, 12))
    if "last date of the month" in rules_lower:
        return _last_date_of_month(rng), month_anchor or (rng.randint(2018, 2026), rng.randint(1, 12))
    year, month = month_anchor or (rng.randint(2018, 2026), rng.randint(1, 12))
    anchor = (year, month)
    if "any date in the month" in rules_lower or "any datetime in the month" in rules_lower:
        return _random_date_in_month(rng, year, month), anchor
    return _random_date_in_month(rng, year, month), anchor


def _generate_datetime_value(
    rng: random.Random,
    rules: str,
    rules_lower: str,
    context: Mapping[str, str],
    month_anchor: Optional[Tuple[int, int]],
) -> Tuple[str, Tuple[int, int]]:
    """Produce a **Datetime** column value (``YYYY-MM-DD HH:MM:SS``).

    Purpose
    -------
    Format all Datetime datatypes with time component; honor caps like ``< TXN_DT``.

    Example invocation
    ------------------
    ``val, _ = _generate_datetime_value(rng, "9999-12-31 00:00:00", "...", {}, None)``
    """

    if "9999-12-31" in rules:
        return "9999-12-31 00:00:00", month_anchor or (rng.randint(2018, 2026), rng.randint(1, 12))
    if "last date of the month" in rules_lower:
        return _last_datetime_of_month(rng), month_anchor or (rng.randint(2018, 2026), rng.randint(1, 12))

    year, month = month_anchor or (rng.randint(2018, 2026), rng.randint(1, 12))
    anchor = (year, month)

    for field_ref in _CROSS_FIELD_LT_RE.findall(rules):
        cap_dt = _context_datetime(context, field_ref)
        if cap_dt is not None:
            return _random_datetime_in_month_before(rng, year, month, cap_dt), anchor

    if "any datetime in the month" in rules_lower or "any date in the month" in rules_lower:
        return _random_datetime_in_month(rng, year, month), anchor

    return _random_datetime_in_month(rng, year, month), anchor


def _format_zero_padded_unique(n: int, width: int) -> Optional[str]:
    """Format a positive integer as zero-padded digits exactly ``width`` wide.

    Purpose
    -------
    For **Unique Number** rules, values are only decimal digits with leading
    zeros (no column-name prefixes or alphanumeric encodings).

    Internal Logic
    ----------------
    Return ``str(n).zfill(width)`` when ``1 <= n < 10**width``; otherwise
    ``None`` (serial exhausted for that width).

    Example invocation
    ------------------
    ``_format_zero_padded_unique(7, 3)`` → ``"007"``
    ``_format_zero_padded_unique(1000, 3)`` → ``None``
    """

    if width <= 0:
        width = 1
    if n <= 0 or n >= 10**width:
        return None
    return str(n).zfill(width)


def _generate_unique_number(
    spec: ColumnSpec,
    row_index: int,
    issued: Dict[str, set[str]],
) -> str:
    """Build a unique string for **Unique Number** rules.

    Purpose
    -------
    Primary keys and IDs must not repeat across generated rows.

    Internal Logic
    ----------------
    Assign serial ``row_index + 1`` (then increment on collision) as a
    zero-padded decimal via :func:`_format_zero_padded_unique`. Column names
    are never embedded in the value.

    Example invocation
    ------------------
    ``_generate_unique_number(ColumnSpec("LOAN_NBR", "Char", 3, "Unique Number"), 6, {})``
    → ``"007"``
    """

    width = spec.field_length or 12
    seen = issued.setdefault(spec.name, set())
    capacity = 10**width
    for offset in range(capacity):
        n = row_index + offset + 1
        value = _format_zero_padded_unique(n, width)
        if value is None:
            break
        if value not in seen:
            seen.add(value)
            return value
    raise ValueError(
        f"Could not allocate a unique numeric value for {spec.name!r} "
        f"at width {width} (max {capacity - 1} distinct values)."
    )


class RuleValueGenerator:
    """Generate one cell value from a :class:`ColumnSpec` and row context.

    Purpose
    -------
    Centralize rule matching so column order and cross-field dependencies stay
    consistent.

    Internal Logic
    ----------------
    Rules text is matched case-insensitively with keyword / regex handlers for
    enums, numerics, dates, geography, literals, and uniqueness.

    Example invocation
    ------------------
    ``gen = RuleValueGenerator(random.Random(42)); gen.generate(spec, 0, {})``
    """

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self._rng = rng or random.Random()
        self._unique_issued: Dict[str, set[str]] = {}
        self._month_anchor: Optional[Tuple[int, int]] = None

    def generate(
        self,
        spec: ColumnSpec,
        row_index: int,
        context: Mapping[str, str],
    ) -> str:
        """Return one delimited-field value for ``spec`` at ``row_index``.

        Parameters
        ----------
        spec:
            Column metadata including rules and datatype.
        row_index:
            Zero-based record index (used for unique keys).
        context:
            Already-generated columns in the current row (name → value).

        Returns
        -------
        str
            String cell value (no delimiter).
        """

        rules = (spec.rules or "").strip()
        rules_lower = rules.lower()
        dtype = spec.data_type.strip().lower()

        if "unique number" in rules_lower:
            return _generate_unique_number(spec, row_index, self._unique_issued)

        if "us states only" in rules_lower:
            state = self._rng.choice(_US_STATES)
            return _apply_char_length(state, spec.field_length)

        if "counties" in rules_lower and "state" in rules_lower:
            state_val = ""
            for key, val in context.items():
                if key.upper() == "STATE":
                    state_val = val.strip().upper()
                    break
            counties = _COUNTIES_BY_STATE.get(state_val, _DEFAULT_COUNTIES)
            county = self._rng.choice(counties)
            return _apply_char_length(county, spec.field_length)

        if dtype in ("num", "number", "numeric", "decimal", "float", "int", "integer"):
            low, high, _ = _parse_numeric_rule(rules, context)
            if low is not None or high is not None or "<" in rules:
                return _random_numeric(self._rng, low, high, as_int=True)
            return _random_numeric(self._rng, 1.0, 100_000.0, as_int=True)

        if dtype == "date":
            value, anchor = _generate_date_value(
                self._rng, rules, rules_lower, self._month_anchor
            )
            self._month_anchor = anchor
            return value

        if dtype == "datetime":
            value, anchor = _generate_datetime_value(
                self._rng, rules, rules_lower, context, self._month_anchor
            )
            self._month_anchor = anchor
            return value

        # Comma-separated pick list (payment method, etc.).
        if "," in rules and not any(op in rules for op in (">", "<")):
            options = [o.strip() for o in _ENUM_SPLIT_RE.split(rules) if o.strip()]
            if options:
                choice = self._rng.choice(options)
                return _apply_char_length(choice, spec.field_length)

        # Single literal (e.g. ACTIVE = ``A``).
        if rules and "<" not in rules and ">" not in rules and "," not in rules:
            return _apply_char_length(rules, spec.field_length)

        # Fallback by datatype.
        if dtype in ("char", "string", "text"):
            return _apply_char_length(f"{spec.name}_{row_index + 1}", spec.field_length)

        return str(row_index + 1)


def generate_record(
    specs: Sequence[ColumnSpec],
    row_index: int,
    generator: RuleValueGenerator,
) -> List[str]:
    """Build one pipe-delimited row in column order.

    Purpose
    -------
    Apply :class:`RuleValueGenerator` sequentially so cross-field rules work.

    Example invocation
    ------------------
    ``row = generate_record(specs, 0, RuleValueGenerator(random.Random(0)))``
    """

    context: Dict[str, str] = {}
    values: List[str] = []
    for spec in specs:
        value = generator.generate(spec, row_index, context)
        context[spec.name] = value
        values.append(value)
    return values


def write_pipe_delimited_file(
    specs: Sequence[ColumnSpec],
    record_count: int,
    output_path: Path,
    *,
    rng: Optional[random.Random] = None,
    include_header: bool = True,
) -> None:
    """Write header and *record_count* body rows to ``output_path``.

    Purpose
    -------
    Persist the synthetic pipe file for File Analyzer or other tools.

    Internal Logic
    ----------------
    Opens the output file as UTF-8 text, writes field names joined by ``|``,
    then one generated row per index.

    Parameters
    ----------
    specs:
        Column definitions from :func:`read_generation_metadata`.
    record_count:
        Number of data rows (excluding header).
    output_path:
        Destination file path (parent dirs created if needed).
    rng:
        Optional seeded RNG for reproducible output.
    include_header:
        When True, first line is column names.

    Example invocation
    ------------------
    ``write_pipe_delimited_file(specs, 100, Path("sample/out.txt"), rng=random.Random(1))``
    """

    if record_count < 0:
        raise ValueError(f"record_count must be non-negative, got {record_count}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    gen = RuleValueGenerator(rng)

    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        if include_header:
            handle.write("|".join(spec.name for spec in specs) + "\n")
        for idx in range(record_count):
            row = generate_record(specs, idx, gen)
            handle.write("|".join(row) + "\n")

    logger.info("Wrote %s records (%s columns) to %s", record_count, len(specs), output_path)


def _default_output_path(meta_path: Path) -> Path:
    """Derive ``sample/<stem>_generated.txt`` under the repository root.

    Purpose
    -------
    Provide a sensible default when ``--output`` is omitted.

    Example invocation
    ------------------
    ``_default_output_path(Path("templates/LoanPop.xlsx"))``
    → ``<repo>/sample/LoanPop_generated.txt``
    """

    return _repo_root() / "sample" / f"{meta_path.stem}_generated.txt"


def build_arg_parser() -> argparse.ArgumentParser:
    """Configure CLI arguments for metadata path, record count, and output.

    Purpose
    -------
    Expose defaults: ``templates/LoanPop.xlsx`` and ``100`` records.

    Example invocation
    ------------------
    ``parser = build_arg_parser(); args = parser.parse_args(["--records", "50"])``
    """

    root = _repo_root()
    default_meta = root / "templates" / "LoanPop.xlsx"

    parser = argparse.ArgumentParser(
        description="Generate a fictitious pipe-delimited file from Excel metadata.",
    )
    parser.add_argument(
        "--meta-path",
        type=Path,
        default=default_meta,
        help=f"Excel metadata workbook (default: {default_meta})",
    )
    parser.add_argument(
        "--records",
        "-n",
        type=int,
        default=100,
        help="Number of data records to generate (default: 100)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output file path (default: sample/<meta_stem>_generated.txt)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible data",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Omit the header row of field names",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO logging",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry: read metadata, generate rows, write pipe file.

    Purpose
    -------
    Wire argparse → :func:`read_generation_metadata` →
    :func:`write_pipe_delimited_file`.

    Returns
    -------
    int
        ``0`` on success, ``1`` on user or validation errors.

    Example invocation
    ------------------
    ``raise SystemExit(main())``
    """

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    meta_path: Path = args.meta_path
    if not meta_path.is_absolute():
        meta_path = (_repo_root() / meta_path).resolve()

    try:
        specs = read_generation_metadata(meta_path)
    except (FileNotFoundError, ValueError, ImportError) as exc:
        logger.error("%s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.records < 0:
        print("Error: --records must be non-negative.", file=sys.stderr)
        return 1

    output_path: Path = args.output if args.output is not None else _default_output_path(meta_path)
    if not output_path.is_absolute():
        output_path = (_repo_root() / output_path).resolve()

    rng = random.Random(args.seed) if args.seed is not None else None

    try:
        write_pipe_delimited_file(
            specs,
            args.records,
            output_path,
            rng=rng,
            include_header=not args.no_header,
        )
    except OSError as exc:
        logger.exception("Failed to write output file")
        print(f"Error writing {output_path}: {exc}", file=sys.stderr)
        return 1

    print(f"Generated {args.records} records -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
