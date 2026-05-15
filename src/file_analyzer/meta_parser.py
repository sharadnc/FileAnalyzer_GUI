"""Metadata parser for File Analyzer CSV/Pipe datasets.

This module implements parsing for the project's hybrid ``_Meta`` file format.
The metadata format contains:

1. A comma-separated list of primary-key column names (FileKey columns).
2. Field definitions encoded as pipe-delimited records:
   ``FieldName|FieldType|FieldLength|FieldDesc|FieldDType``, where ``FieldDType``
   is expected to be ``D`` (dimension) or ``M`` (measure).

Excel ``.xlsx`` / ``.xls`` workbooks are also supported: see :func:`parse_meta_from_excel`
for the column layout (**Field Name**, **Primary Key**, **FieldType**, **Datatype**,
**Field Length**, **Field Description** in any order).

The sample dataset demonstrates a common real-world edge case: the last
FileKey column name may be concatenated with the first field definition
beginning, leading to a boundary like ``...,DIVISION|SUMLEV|Char|...``.

To handle this robustly, the parser uses a regex to locate *valid* field
definition records and then infers the FileKey prefix from the substring
before the first match.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Tuple

_FieldDType = Literal["D", "M"]


@dataclass(frozen=True)
class FieldMeta:
    """Metadata for a single column/field in the dataset.

    Purpose
    -------
    Provide a structured representation of a column description extracted from
    the dataset's ``_Meta`` file.

    Internal Logic
    ---------------
    Instances are created by :func:`parse_field_def` and populated with the
    five core parts encoded in each pipe-delimited field definition record.

    Parameters
    ----------
    name:
        Column name (as it appears in the data header).
    field_type:
        Data semantic type, e.g. ``Char``, ``Num``, ``Date``, ``Datetime``.
    field_length:
        Optional length/precision string. Some numeric fields use an empty
        length in the metadata (e.g. ``Num||Base``).
    description:
        Human-friendly description for UI tooltips and hover panels.
    field_dtype:
        ``D`` for dimensions or ``M`` for measures.
    """

    name: str
    field_type: str
    field_length: Optional[str]
    description: str
    field_dtype: _FieldDType


def normalized_field_type(field_type: str) -> str:
    """Normalize ``FieldType`` for comparisons (alphanumeric, uppercased).

    Purpose
    -------
    Excel and text meta may use mixed case or punctuation; this yields one token
    for rules such as ``DISPLAY`` and ``YYYYMMDD``.

    Example invocation
    --------------------
    ``normalized_field_type(\" yyyymmdd \")`` → ``\"YYYYMMDD\"``
    """

    return "".join(ch for ch in field_type.strip().upper() if ch.isalnum())


def is_display_only_field(field: FieldMeta) -> bool:
    """Return True when ``FieldType`` is ``DISPLAY`` (grid columns only, no D/M panels)."""

    return normalized_field_type(field.field_type) == "DISPLAY"


def is_yyyymmdd_field_type(field_type: str) -> bool:
    """Return True for calendar-key ``FieldType`` values (treated as dimensions in panels)."""

    return normalized_field_type(field_type) == "YYYYMMDD"


def field_in_dimension_panels(field: FieldMeta) -> bool:
    """Return True if the field may appear in Dimension shelves (Visualize, Pivot, filters).

    Internal Logic
    ----------------
    - ``DISPLAY`` → False (data grids only).
    - ``YYYYMMDD`` / common spellings → True (dimension behavior even if Datatype is ``M``).
    - Otherwise → ``field_dtype == 'D'``.
    """

    if is_display_only_field(field):
        return False
    if is_yyyymmdd_field_type(field.field_type):
        return True
    return field.field_dtype == "D"


def field_in_measure_panels(field: FieldMeta) -> bool:
    """Return True if the field may appear in Measure shelves (Values, measure filters)."""

    if is_display_only_field(field):
        return False
    if is_yyyymmdd_field_type(field.field_type):
        return False
    return field.field_dtype == "M"


def field_formats_as_measure(field: FieldMeta) -> bool:
    """Return True when grids/charts should use measure numeric formatting."""

    if is_display_only_field(field):
        return field.field_dtype == "M"
    if is_yyyymmdd_field_type(field.field_type):
        return False
    return field.field_dtype == "M"


def field_displays_as_yyyymmdd(field: FieldMeta) -> bool:
    """Return True when table cells should show eight-digit ``YYYYMMDD`` text.

    Purpose
    -------
    Fields with ``FieldType`` ``YYYYMMDD`` (often ``Datatype`` Date/Datetime in Excel)
    appear on dimension shelves and must render as ``20240515``-style values in grids.

    Example invocation
    --------------------
    ``field_displays_as_yyyymmdd(field)`` before formatting a cell value.
    """

    return is_yyyymmdd_field_type(field.field_type)


def field_quick_stats_use_yyyymmdd_values(field: FieldMeta) -> bool:
    """Return True when quick-stats value/count rows should use ``YYYYMMDD`` text.

    Purpose
    -------
    Visualize and Pivot hover tooltips must show eight-digit dates for
    ``FieldType`` ``YYYYMMDD`` and for storage types ``Date`` / ``Datetime`` /
    ``Timestamp`` (and ``Time``), matching grid display rules.

    Internal Logic
    ----------------
    1. ``YYYYMMDD`` role token → True.
    2. Lowercased ``field_type`` in ``date``, ``datetime``, ``timestamp``, ``time`` → True.
    3. Normalized alphanumeric token in ``DATE``, ``DATETIME``, ``TIMESTAMP``, ``TIME`` → True.

    Example invocation
    --------------------
    ``field_quick_stats_use_yyyymmdd_values(due_dt_field)`` → True for ``FieldType`` ``Date``.
    """

    if is_yyyymmdd_field_type(field.field_type):
        return True
    t = field.field_type.lower().strip()
    if t in ("date", "datetime", "timestamp", "time"):
        return True
    role = normalized_field_type(field.field_type)
    return role in ("DATE", "DATETIME", "TIMESTAMP", "TIME")


def format_yyyymmdd_display(value: object) -> str:
    """Format a date-like cell as ``YYYYMMDD`` (8 digits, no separators).

    Purpose
    -------
    Normalize DuckDB/pandas date representations for Visualize, Data Grid, and
    Pivot table display when metadata marks the column as ``FieldType`` ``YYYYMMDD``.

    Internal Logic
    ----------------
    1. Handle ``None`` / NA.
    2. Use :class:`datetime.date` / :class:`datetime.datetime` ``strftime`` when available.
    3. Accept integer or whole-float compact forms (for example ``20240515``).
    4. Strip non-digits from strings that already look like eight-digit dates.
    5. Otherwise parse with ``pandas.to_datetime`` and format to ``%Y%m%d``.

    Example invocation
    --------------------
    ``format_yyyymmdd_display("2024-05-15")`` → ``"20240515"``
    ``format_yyyymmdd_display(20240515.0)`` → ``"20240515"``

    Args:
        value: Raw cell value from DuckDB or an in-memory chart row.

    Returns:
        Eight-digit date string, or a safe fallback string when parsing fails.
    """

    if value is None:
        return ""
    try:
        import pandas as pd

        if pd.isna(value):
            return ""
    except Exception:
        pass

    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")

    if isinstance(value, bool):
        return str(value)

    try:
        if isinstance(value, int):
            compact = value
        else:
            as_float = float(value)
            if as_float != as_float or as_float in (float("inf"), float("-inf")):
                return "—"
            if as_float != int(as_float):
                raise ValueError("not a compact YYYYMMDD integer")
            compact = int(as_float)
        if 10000101 <= compact <= 99991231:
            return f"{compact:08d}"
    except (TypeError, ValueError, OverflowError):
        pass

    s = str(value).strip()
    if not s or s.lower() == "nan":
        return ""

    digits_only = re.sub(r"\D", "", s)
    if len(digits_only) == 8 and digits_only.isdigit():
        return digits_only

    try:
        import pandas as pd

        ts = pd.to_datetime(s, errors="coerce")
        if pd.isna(ts):
            return s
        return pd.Timestamp(ts).strftime("%Y%m%d")
    except Exception:
        return s


@dataclass(frozen=True)
class MetaDefinition:
    """Parsed representation of a dataset's hybrid metadata file.

    Purpose
    -------
    Hold the primary-key column names and the full ordered field metadata.

    Internal Logic
    ---------------
    Instances are created by :func:`parse_meta_line` or
    :func:`parse_meta_file`. The parser preserves ordering from the metadata
    file, which is later used for UI lists and default chart behavior.

    Parameters
    ----------
    file_key_columns:
        Ordered list of primary-key column names.
    fields:
        Ordered list of :class:`FieldMeta` entries.
    """

    file_key_columns: List[str]
    fields: List[FieldMeta]

    @property
    def fields_by_name(self) -> Dict[str, FieldMeta]:
        """Map of field name to its metadata.

        Purpose
        -------
        Allow quick lookup of metadata by field name.

        Returns
        -------
        Dict[str, FieldMeta]
            Mapping from field name to metadata.
        """

        return {f.name: f for f in self.fields}

    def dimensions(self) -> List[FieldMeta]:
        """Return fields eligible for dimension panels (see :func:`field_in_dimension_panels`)."""

        return [f for f in self.fields if field_in_dimension_panels(f)]

    def measures(self) -> List[FieldMeta]:
        """Return fields eligible for measure panels (see :func:`field_in_measure_panels`)."""

        return [f for f in self.fields if field_in_measure_panels(f)]


_FIELD_DEF_RE = re.compile(
    r"(?P<name>[^|,\n\r]+)\|"
    r"(?P<ftype>[^|,\n\r]+)\|"
    r"(?P<flen>[^|,\n\r]*)\|"
    r"(?P<fdesc>[^|,\n\r]+)\|"
    r"(?P<fdtype>[DM])"
)


def parse_field_def(field_def_text: str) -> FieldMeta:
    """Parse one pipe-delimited field definition record.

    Purpose
    -------
    Convert ``FieldName|FieldType|FieldLength|FieldDesc|FieldDType`` into a
    structured :class:`FieldMeta` object.

    Internal Logic
    ---------------
    The function splits the input on ``|`` and validates that exactly five
    segments are present. Empty field-length values are converted to ``None``.

    Parameters
    ----------
    field_def_text:
        Single field definition record, expected to contain exactly four pipe
        separators.

    Returns
    -------
    FieldMeta
        Parsed field metadata.

    Raises
    ------
    ValueError
        If the record does not contain exactly five ``|``-separated parts or if
        the ``FieldDType`` is invalid.

    Example
    -------
    >>> parse_field_def("ESTIMATESBASE2020|Num||Base|M").field_length is None
    True
    """

    parts = field_def_text.split("|")
    if len(parts) != 5:
        raise ValueError(
            f"Invalid field definition. Expected 5 parts but got {len(parts)}: {field_def_text}"
        )

    name, field_type, field_length_raw, description, field_dtype_raw = (p.strip() for p in parts)

    if field_dtype_raw not in ("D", "M"):
        raise ValueError(
            f"Invalid field dtype '{field_dtype_raw}'. Expected 'D' or 'M'."
        )

    field_length = field_length_raw if field_length_raw != "" else None

    return FieldMeta(
        name=name,
        field_type=field_type,
        field_length=field_length,
        description=description,
        field_dtype=field_dtype_raw,  # validated above
    )


def _infer_file_key_columns(meta_line: str) -> List[str]:
    """Infer FileKey columns from the prefix before the first valid field def.

    Purpose
    -------
    Resolve the real-world boundary issue where the last FileKey token may be
    concatenated into the first field definition record.

    Internal Logic
    ---------------
    1. Find the earliest regex match corresponding to a valid field definition.
    2. Take the substring before that match and treat it as the comma-separated
       FileKey prefix.
    3. Remove trailing ``|`` (if present) from the prefix and split by comma.
    4. Trim whitespace and drop empty tokens.

    Parameters
    ----------
    meta_line:
        Full metadata line content.

    Returns
    -------
    List[str]
        Ordered list of file-key column names.

    Raises
    ------
    ValueError
        If no valid field definition is found (cannot infer file keys).
    """

    first_match = next(_FIELD_DEF_RE.finditer(meta_line), None)
    if first_match is None:
        raise ValueError("Could not find any valid field definition records in meta line.")

    prefix = meta_line[: first_match.start()]
    prefix = prefix.rstrip("| \t")
    file_keys = [p.strip() for p in prefix.split(",") if p.strip() != ""]
    if not file_keys:
        raise ValueError("Inferred FileKey prefix is empty; meta line format might be invalid.")
    return file_keys


def parse_meta_line(meta_line: str) -> MetaDefinition:
    """Parse a metadata line into a structured :class:`MetaDefinition`.

    Purpose
    -------
    Convert the project's hybrid ``_Meta`` line into:
    - ordered FileKey column list, and
    - ordered field definitions list.

    Internal Logic
    ---------------
    The parser:
    1. Trims whitespace and validates non-empty input.
    2. Uses :data:`_FIELD_DEF_RE` to find all valid field definition records and
       parse them in-order.
    3. Infers FileKey columns from the prefix before the first field definition
       match.
    4. Validates that FileKey columns exist among parsed field names
       (best-effort; UI can still function even if mismatch occurs).

    Parameters
    ----------
    meta_line:
        A single line content read from a ``*_Meta`` file.

    Returns
    -------
    MetaDefinition
        Parsed metadata representation.

    Raises
    ------
    ValueError
        If input is empty or if parsing fails due to format mismatch.

    Example
    -------
    >>> md = parse_meta_line(
    ...     "SUMLEV,REGION,DIVISION|SUMLEV|Char|3|Level|D,REGION|Char|50|REGION|D"
    ... )
    >>> md.file_key_columns[0]
    'SUMLEV'
    """

    line = meta_line.strip()
    if not line:
        raise ValueError("meta_line is empty.")

    file_keys = _infer_file_key_columns(line)

    fields: List[FieldMeta] = []
    for match in _FIELD_DEF_RE.finditer(line):
        full_text = match.group(0)
        fields.append(parse_field_def(full_text))

    if not fields:
        raise ValueError("No fields were parsed from the meta line.")

    # Best-effort validation: the UI expects FileKey names to appear in fields.
    field_names = {f.name for f in fields}
    missing_keys = [k for k in file_keys if k not in field_names]
    if missing_keys:
        # Keep parsing resilient; raise only if keys are completely unrelated.
        # For this project's sample data, keys should match.
        raise ValueError(
            f"Inferred FileKey columns are not present in parsed fields: {missing_keys}"
        )

    return MetaDefinition(file_key_columns=file_keys, fields=fields)


def _normalize_excel_header(value: object) -> str:
    """Normalize a spreadsheet column header for fuzzy matching.

    Purpose
    -------
    Excel templates may label columns with spaces or different casing; this
    produces a single comparable token (alphanumeric only, lowercased).

    Internal Logic
    ----------------
    Strip the string, keep only ``[a-z0-9]``, lowercase.

    Example invocation
    ------------------
    >>> _normalize_excel_header("Field Name")
    'fieldname'
    """

    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


def _excel_column_slot_for_header(norm: str) -> Optional[str]:
    """Map a normalized header token to a canonical slot name, if recognized."""

    if norm in ("fieldname", "columnname", "name"):
        return "field_name"
    if norm in ("primarykey", "pk", "iskey", "key"):
        return "primary_key"
    if norm in ("fieldtype", "ftype", "semantic", "semantictype"):
        return "field_type"
    if norm in ("datatype", "fielddtype", "dm", "fielddm", "dimensionmeasure", "role"):
        return "data_type_dm"
    if norm in ("fieldlength", "length", "flen", "precision"):
        return "field_length"
    if norm in ("fielddescription", "description", "desc", "fielddesc", "comment"):
        return "field_description"
    return None


def _resolve_excel_meta_column_map(columns: Sequence[str]) -> Dict[str, str]:
    """Build ``slot -> original column label`` for the six expected Excel roles.

    Purpose
    -------
    Required columns (by meaning) can appear in any order; this inspects
    header names only.

    Internal Logic
    ----------------
    Walk ``columns`` in order; for each header compute :func:`_normalize_excel_header`
    and :func:`_excel_column_slot_for_header`; assign the first column seen per slot.

    Example invocation
    ------------------
    Used only from :func:`parse_meta_from_excel`.
    """

    mapping: Dict[str, str] = {}
    for col in columns:
        slot = _excel_column_slot_for_header(_normalize_excel_header(col))
        if slot is None or slot in mapping:
            continue
        mapping[slot] = str(col).strip()
    return mapping


# Canonical Excel metadata columns (slot, user-facing label).
REQUIRED_EXCEL_META_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("field_name", "Field Name"),
    ("primary_key", "Primary Key"),
    ("field_type", "FieldType"),
    ("data_type_dm", "Datatype"),
    ("field_length", "Field Length"),
    ("field_description", "Field Description"),
)


def missing_required_excel_meta_columns(columns: Sequence[str]) -> List[str]:
    """Return display labels for required Excel meta columns that are not present.

    Purpose
    -------
    Support pre-load validation and error messages before parsing row data.

    Internal Logic
    ----------------
    Map headers with :func:`_resolve_excel_meta_column_map` and compare against
    :data:`REQUIRED_EXCEL_META_COLUMNS`.

    Example invocation
    --------------------
    ``missing_required_excel_meta_columns(["Field Name", "Datatype"])``
    → ``[\"Primary Key\", \"FieldType\", \"Field Length\", \"Field Description\"]``
    """

    col_map = _resolve_excel_meta_column_map(columns)
    return [label for slot, label in REQUIRED_EXCEL_META_COLUMNS if slot not in col_map]


def format_excel_meta_columns_popup_message(
    missing_labels: Sequence[str],
    found_columns: Optional[Sequence[str]] = None,
) -> str:
    """Build the warning text shown when Excel metadata headers are incomplete.

    Purpose
    -------
    Used by the welcome screen popup and by :func:`parse_meta_from_excel` errors.

    Internal Logic
    ----------------
    List all required column titles, then missing names and optional found headers.

    Example invocation
    --------------------
    ``format_excel_meta_columns_popup_message([\"Field Length\"], [\"Field Name\"])``
    """

    required_line = ", ".join(label for _, label in REQUIRED_EXCEL_META_COLUMNS)
    lines = [
        "The metadata file must include at least these columns:",
        "",
        required_line,
        "",
    ]
    if missing_labels:
        lines.append("Missing or unrecognized column(s):")
        lines.extend(f"  • {name}" for name in missing_labels)
        lines.append("")
    if found_columns is not None:
        found = ", ".join(str(c) for c in found_columns)
        lines.append(f"Columns found in the file: {found}")
    return "\n".join(lines).rstrip()


def validate_meta_file_before_load(meta_path: str | Path) -> Optional[str]:
    """Validate Excel metadata headers before loading a dataset.

    Purpose
    -------
    Let the UI warn the user immediately (popup) when a workbook is missing required
    columns. Text ``*_Meta`` files use the legacy pipe format and are not checked here.

    Internal Logic
    ----------------
    1. Return ``None`` when the path is not ``.xlsx`` / ``.xls``.
    2. Read column headers only via ``pandas.read_excel(..., nrows=0)``.
    3. If :func:`missing_required_excel_meta_columns` is non-empty, return a
       :func:`format_excel_meta_columns_popup_message` string; else ``None``.

    Example invocation
    --------------------
    ``msg = validate_meta_file_before_load(Path(\"templates/LoanPop.xlsx\"))``
    """

    path = Path(meta_path)
    suffix = path.suffix.lower()
    if suffix not in (".xlsx", ".xls"):
        return None
    if not path.exists():
        return None

    try:
        import pandas as pd
    except ImportError:
        return None

    engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
    try:
        df = pd.read_excel(path, engine=engine, nrows=0)
    except Exception as exc:
        return f"Could not read metadata workbook headers from {path}:\n{exc}"

    missing = missing_required_excel_meta_columns(list(df.columns))
    if not missing:
        return None
    return format_excel_meta_columns_popup_message(missing, list(df.columns))


def _excel_truthy_primary_key(value: object) -> bool:
    """Return True when an Excel cell marks a row as part of the FileKey."""

    if value is None:
        return False
    try:
        import pandas as pd

        if pd.isna(value):
            return False
    except Exception:
        pass
    s = str(value).strip().upper()
    return s in ("Y", "YES", "TRUE", "1", "T")


def _excel_field_dtype(value: object) -> _FieldDType:
    """Interpret the Datatype / D-M column into ``D`` or ``M``."""

    if value is None:
        raise ValueError("Datatype cell is empty; expected D or M (or synonyms).")
    try:
        import pandas as pd

        if pd.isna(value):
            raise ValueError("Datatype cell is empty; expected D or M (or synonyms).")
    except Exception:
        pass
    s = str(value).strip().upper()
    if s in (
        "D",
        "DIM",
        "DIMENSION",
        "CHAR",
        "STRING",
        "TEXT",
        "VARCHAR",
        "VARCHAR2",
        "NVARCHAR",
        "NVARCHAR2",
    ):
        return "D"
    if s in (
        "M",
        "MEAS",
        "MEASURE",
        "NUM",
        "NUMBER",
        "FLOAT",
        "INT",
        "INTEGER",
        "DECIMAL",
        "DATE",
        "DATETIME",
        "TIMESTAMP",
        "TIME",
    ):
        return "M"
    raise ValueError(f"Unrecognized Datatype value {value!r}; expected D or M (or common synonyms).")


def _excel_optional_cell_str(value: object) -> Optional[str]:
    """Convert a spreadsheet cell to a stripped string or ``None`` when blank."""

    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:
        pass
    s = str(value).strip()
    return s if s else None


def _excel_field_length_cell_str(value: object) -> Optional[str]:
    """Convert a **Field Length** cell to a string, normalizing whole-number floats.

    Purpose
    -------
    Spreadsheets often store lengths as numeric cells; ``pandas`` then yields
    ``float`` values (for example ``10.0``). The project expects the canonical
    text form (``\"10\"``) to match text-based ``_Meta`` parsing.

    Internal Logic
    ----------------
    1. Return ``None`` for blank / NA cells (same rules as :func:`_excel_optional_cell_str`).
    2. Skip ``bool`` so ``True``/``False`` are not coerced through the integer path.
    3. For :class:`numbers.Integral` scalars, return ``str(int(value))``.
    4. For finite floats whose value equals an integer, return ``str(int(value))``.
    5. Otherwise return stripped ``str(value)``, or ``None`` if empty.

    Example invocation
    --------------------
    ``assert _excel_field_length_cell_str(10.0) == "10"``
    ``assert _excel_field_length_cell_str(" 12.5 ") == "12.5"``
    """

    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, bool):
        s = str(value).strip()
        return s if s else None

    try:
        import numbers

        if isinstance(value, numbers.Integral):
            return str(int(value))
    except Exception:
        pass

    try:
        as_float = float(value)
        if math.isfinite(as_float) and as_float == int(as_float):
            return str(int(as_float))
    except (TypeError, ValueError, OverflowError):
        pass

    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def parse_meta_from_excel(meta_path: str | Path) -> MetaDefinition:
    """Build :class:`MetaDefinition` from a spreadsheet with the standard meta columns.

    Purpose
    -------
    Support ``.xlsx`` / ``.xls`` metadata selected via **Browse meta** or
    **Browse Templates** so FileKey and field rows do not require the legacy
    single-line ``_Meta`` text format.

    Internal Logic
    ----------------
    1. Load the first worksheet with ``pandas.read_excel`` (``openpyxl`` engine for ``.xlsx``).
    2. Map headers to slots using :func:`_resolve_excel_meta_column_map` (any column order).
    3. Collect ``file_key_columns`` as field names whose **Primary Key** cell is truthy
       (``Y``, ``Yes``, ``1``, …), preserving sheet row order.
    4. Build one :class:`FieldMeta` per non-empty **Field Name**, mapping
       **FieldType** → ``field_type``, **Datatype** → ``field_dtype`` (``D``/``M``),
       **Field Length** → ``field_length``, **Field Description** → ``description``.

    Parameters
    ----------
    meta_path:
        Path to ``.xlsx`` or ``.xls`` metadata.

    Returns
    -------
    MetaDefinition
        Same structure as :func:`parse_meta_line` / :func:`parse_meta_file` for text meta.

    Raises
    ------
    ImportError
        If ``pandas`` or ``openpyxl`` is not installed.
    ValueError
        If required columns are missing or rows are invalid.

    Example invocation
    ------------------
    ``md = parse_meta_from_excel(Path("templates/dataset_meta.xlsx"))``
    """

    path = Path(meta_path)
    if not path.exists():
        raise FileNotFoundError(f"Meta file not found: {path}")

    try:
        import pandas as pd
    except ImportError as e:  # pragma: no cover
        raise ImportError("Reading Excel metadata requires pandas.") from e

    suffix = path.suffix.lower()
    engine = "openpyxl" if suffix == ".xlsx" else "xlrd" if suffix == ".xls" else None
    if engine is None:
        raise ValueError(f"Unsupported Excel extension for meta: {path.suffix}")

    if engine == "openpyxl":
        try:
            import openpyxl  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise ImportError('Install openpyxl to read .xlsx metadata (pip install "openpyxl>=3.1").') from e

    try:
        df = pd.read_excel(path, engine=engine)
    except Exception as e:
        raise ValueError(f"Failed to read Excel metadata from {path}: {e}") from e

    if df.empty:
        raise ValueError(f"Excel metadata sheet is empty: {path}")

    col_map = _resolve_excel_meta_column_map(list(df.columns))
    missing_labels = missing_required_excel_meta_columns(list(df.columns))
    if missing_labels:
        raise ValueError(
            format_excel_meta_columns_popup_message(missing_labels, list(df.columns))
        )

    c_fn = col_map["field_name"]
    c_pk = col_map["primary_key"]
    c_ft = col_map["field_type"]
    c_dt = col_map["data_type_dm"]
    c_fl = col_map["field_length"]
    c_fd = col_map["field_description"]

    file_keys: List[str] = []
    fields: List[FieldMeta] = []
    seen_names: set[str] = set()

    for _, row in df.iterrows():
        raw_name = row.get(c_fn, None)
        name = _excel_optional_cell_str(raw_name)
        if not name or str(name).lower() == "nan":
            continue
        if name in seen_names:
            raise ValueError(f"Duplicate Field Name in Excel metadata: {name!r}")
        seen_names.add(name)

        if _excel_truthy_primary_key(row.get(c_pk, None)):
            file_keys.append(name)

        field_type = _excel_optional_cell_str(row.get(c_ft, None)) or "Char"
        description = _excel_optional_cell_str(row.get(c_fd, None)) or ""
        field_length = _excel_field_length_cell_str(row.get(c_fl, None))
        field_dtype = _excel_field_dtype(row.get(c_dt, None))

        fields.append(
            FieldMeta(
                name=name,
                field_type=field_type,
                field_length=field_length,
                description=description,
                field_dtype=field_dtype,
            )
        )

    if not fields:
        raise ValueError(f"No field rows with a non-empty Field Name were found in {path}")

    field_names = {f.name for f in fields}
    missing_keys = [k for k in file_keys if k not in field_names]
    if missing_keys:
        raise ValueError(
            f"Primary Key column lists names that are not present as Field Name rows: {missing_keys}"
        )

    if not file_keys:
        raise ValueError(
            "Excel metadata must include at least one Field Name whose Primary Key is Y "
            "(FileKey columns cannot be empty)."
        )

    return MetaDefinition(file_key_columns=file_keys, fields=fields)


def parse_meta_file(meta_path: str | Path) -> MetaDefinition:
    """Parse a ``*_Meta`` text file or Excel ``.xlsx`` / ``.xls`` metadata from disk.

    Purpose
    -------
    Load metadata from disk: either the legacy one-line hybrid format via
    :func:`parse_meta_line`, or :func:`parse_meta_from_excel` for spreadsheets.

    Internal Logic
    ---------------
    1. If the path ends with ``.xlsx`` or ``.xls``, delegate to :func:`parse_meta_from_excel`.
    2. Otherwise read the file as UTF-8 (fall back to latin-1 if needed).
    3. Select the first non-empty line (meta is expected to be a single line).
    4. Parse it using :func:`parse_meta_line`.

    Parameters
    ----------
    meta_path:
        Path to the ``*_Meta`` file or Excel metadata workbook.

    Returns
    -------
    MetaDefinition
        Parsed metadata structure.

    Raises
    ------
    FileNotFoundError
        If the given path does not exist.
    ValueError
        If the file contains no parsable metadata line.
    """

    path = Path(meta_path)
    if not path.exists():
        raise FileNotFoundError(f"Meta file not found: {path}")

    if path.suffix.lower() in (".xlsx", ".xls"):
        return parse_meta_from_excel(path)

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")

    for raw_line in text.splitlines():
        if raw_line.strip():
            return parse_meta_line(raw_line)

    raise ValueError(f"Meta file contains no non-empty lines: {path}")


def expected_delimiter_from_meta_hint(
    meta_field_defs: Sequence[FieldMeta],
) -> Optional[str]:
    """Optional helper: infer delimiter from field names (best-effort).

    Purpose
    -------
    This project primarily uses delimiter chosen by the user, but certain meta
    patterns can help with safe defaults later.

    Internal Logic
    ---------------
    Currently returns ``None`` because the metadata file does not encode the
    data delimiter directly. The function exists to keep future delimiter
    heuristics isolated and testable.

    Parameters
    ----------
    meta_field_defs:
        Parsed field metadata (unused in current implementation).

    Returns
    -------
    Optional[str]
        Placeholder for future delimiter inference.

    Example
    -------
    >>> expected_delimiter_from_meta_hint([])
    None
    """

    _ = meta_field_defs
    return None

