"""Template-driven automated data profiling narrative for the Summary tab.

Purpose
-------
After :class:`~file_analyzer.summary_reports.DatasetSummaryReport` is computed,
render a fixed five-section executive report (file facts, structure, statistics,
patterns, anomalies) as Qt-friendly HTML for a :class:`QLabel`.

Internal Logic
----------------
- Combine :class:`~file_analyzer.ui.models.LoadedDatasetContext` (paths, delimiter)
  with aggregate metrics from the summary report (row counts, field semantics,
  duplicates, pooled numeric ranges).
- Run an optional capped DuckDB pass over string-like columns for lightweight
  ``regexp_matches`` totals (email / IPv4 / ISO-date prefixes).
- Heuristic phrases (structured vs randomized, security risk) are explicitly
  rule-based so the template reads consistently; they are not cryptographic
  judgments.

Example invocation
--------------------
``html = build_automated_profiling_report_html(ctx, report)``
"""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from file_analyzer.summary_reports import DatasetSummaryReport, FieldSummaryReport
from file_analyzer.ui.models import LoadedDatasetContext


def _duck_quote(ident: str) -> str:
    """Return a double-quoted DuckDB identifier (reserved-safe)."""

    return '"' + str(ident).replace('"', '""') + '"'


def human_file_size(num_bytes: int) -> str:
    """Format *num_bytes* as B / KB / MB / GB for display in the report.

    Purpose
    -------
    Human-readable file size next to the template's **File Size** bullet.

    Internal Logic
    ----------------
    Divide by 1024 until under 1024 or units exhausted; two decimal places for
    KB+.

    Example invocation
    ------------------
    >>> human_file_size(512)
    '512 bytes'
    >>> "MB" in human_file_size(3 * 1024 * 1024)
    True
    """

    if num_bytes < 0:
        return "unknown"
    if num_bytes < 1024:
        return f"{num_bytes:,} bytes"
    size = float(num_bytes)
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024.0:
            return f"{size:.2f} {unit}"
    return f"{size / 1024.0:.2f} TB"


def _infer_file_format_label(path: Path) -> str:
    """Map extension and name to a short **True File Type** label."""

    sfx = path.suffix.lower()
    if sfx == ".csv":
        return "CSV (delimited text)"
    if sfx in (".tsv", ".tab"):
        return "TSV (delimited text)"
    if sfx == ".txt":
        return "Plain text (delimited)"
    if sfx in (".json", ".jsonl", ".ndjson"):
        return "JSON / JSON-lines (not inferred from parser)"
    if sfx in (".parquet", ".pq"):
        return "Parquet (columnar binary)"
    if sfx in (".xlsx", ".xls"):
        return "Spreadsheet binary"
    if sfx == "":
        return "Unknown extension (loaded as delimited text)"
    return f"{sfx.upper().lstrip('.')} (loaded as delimited text via DuckDB)"


def _delimiter_label(delimiter: str) -> str:
    """Turn the raw delimiter character into a template-friendly label."""

    if delimiter == "\t":
        return "Tab"
    if delimiter == "|":
        return "Pipe (|)"
    if delimiter == ",":
        return "Comma"
    if delimiter == ";":
        return "Semicolon"
    return html.escape(repr(delimiter))


def _entropy_score(fields: Sequence[FieldSummaryReport], row_count: int) -> float:
    """Average normalized diversity index (0–10) used as the template entropy score."""

    if not fields or row_count <= 0:
        return 0.0
    parts: List[float] = []
    for fr in fields:
        d = max(1, min(int(fr.distinct_count), row_count))
        denom = math.log2(max(2, row_count))
        parts.append(math.log2(float(d)) / denom)
    raw = sum(parts) / len(parts)
    return round(min(10.0, max(0.0, raw * 10.0)), 2)


def _primary_characteristic(
    fields: Sequence[FieldSummaryReport],
    entropy_score: float,
    duplicate_pct: float,
) -> str:
    """Pick structured / unstructured / randomized wording for the template."""

    n = max(1, len(fields))
    high_card_share = sum(1 for fr in fields if fr.is_high_cardinality) / n
    if entropy_score >= 7.2 or high_card_share >= 0.45:
        return "randomized"
    if entropy_score <= 3.4 and duplicate_pct <= 5.0 and high_card_share <= 0.2:
        return "structured"
    return "unstructured"


def _completeness_and_missing(rep: DatasetSummaryReport) -> Tuple[float, int]:
    """Return (completeness %, total missing cell count)."""

    rows = rep.duplicates.total_rows
    ncols = len(rep.fields)
    if rows <= 0 or ncols <= 0:
        return 100.0, 0
    total_cells = rows * ncols
    missing = sum(int(fr.null_count) + int(fr.empty_string_count) for fr in rep.fields)
    complete = 100.0 * (1.0 - (missing / float(total_cells)))
    return round(max(0.0, min(100.0, complete)), 2), missing


def _type_counts(fields: Sequence[FieldSummaryReport]) -> Tuple[int, int, int]:
    """Counts (numeric, text, datetime) for the Statistical Profile section."""

    n_num = sum(1 for fr in fields if fr.inferred_semantic == "numeric")
    n_dt = sum(1 for fr in fields if fr.inferred_semantic == "datetime")
    n_txt = len(fields) - n_num - n_dt
    return n_num, n_txt, n_dt


def _highest_cardinality_field(fields: Sequence[FieldSummaryReport]) -> Tuple[str, int]:
    """Field name and distinct count for the highest-cardinality column."""

    if not fields:
        return "(none)", 0
    best = max(fields, key=lambda fr: int(fr.distinct_count))
    return best.field_name, int(best.distinct_count)


def _global_numeric_span(
    fields: Sequence[FieldSummaryReport],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Pooled min, max, and mean of per-field population std.dev (numeric only)."""

    mins = [float(fr.numeric_min) for fr in fields if fr.numeric_min is not None]
    maxs = [float(fr.numeric_max) for fr in fields if fr.numeric_max is not None]
    stds = [float(fr.numeric_stddev_pop) for fr in fields if fr.numeric_stddev_pop is not None]
    if not mins or not maxs:
        return None, None, None
    std_mean = sum(stds) / len(stds) if stds else None
    return min(mins), max(maxs), std_mean


def _total_iqr_outliers(fields: Sequence[FieldSummaryReport]) -> int:
    """Sum IQR outlier counts across all measure fields that computed them."""

    return sum(int(fr.outlier_count_iqr or 0) for fr in fields)


def _pattern_scan_total(
    database_path: str,
    table_name: str,
    fields: Sequence[FieldSummaryReport],
    max_columns: int = 12,
) -> int:
    """Count regexp hits (timestamps, IPv4, email-like) across string-like columns.

    Purpose
    -------
    Populate **Identified Formats** with a defensible non-zero total when patterns
    exist, without scanning binary blobs outside the relational table.

    Internal Logic
    ----------------
    Open DuckDB read-only, iterate up to *max_columns* columns where semantics
    are ``string`` or ``mixed``, and add three ``COUNT(*) FILTER`` queries using
    ``regexp_matches`` on trimmed VARCHAR casts. Failures return ``0``.
    """

    candidates = [fr for fr in fields if fr.inferred_semantic in ("string", "mixed")][:max_columns]
    if not candidates:
        return 0
    patterns = (
        r"^\d{4}-\d{2}-\d{2}",
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$",
        r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
    )
    total = 0
    try:
        import duckdb  # type: ignore
    except Exception:
        return 0
    try:
        con = duckdb.connect(database=database_path, read_only=True)
    except Exception:
        return 0
    try:
        for fr in candidates:
            col = _duck_quote(fr.field_name)
            cast_expr = f"trim(cast({col} AS VARCHAR))"
            for pat in patterns:
                safe = pat.replace("'", "''")
                sql = (
                    f"SELECT COUNT(*) FILTER (WHERE {col} IS NOT NULL AND "
                    f"regexp_matches({cast_expr}, '{safe}', 'i')) AS n FROM {table_name}"
                )
                row = con.execute(sql).fetchone()
                total += int(row[0] or 0)
    except Exception:
        return 0
    finally:
        try:
            con.close()
        except Exception:
            pass
    return total


def _chronological_sentence(fields: Sequence[FieldSummaryReport]) -> str:
    """Yes/No sentence for sequential timelines (metadata + inferred datetime)."""

    dt_names = [fr.field_name for fr in fields if fr.inferred_semantic == "datetime"]
    if not dt_names:
        return "No — no date/time semantics were inferred on loaded columns."
    shown = ", ".join(html.escape(n) for n in dt_names[:6])
    more = "" if len(dt_names) <= 6 else f" (+{len(dt_names) - 6} more)"
    return f"Yes — timeline-capable fields: {shown}{more}."


def _security_risk_label(fields: Sequence[FieldSummaryReport], row_count: int) -> str:
    """Return **High** or **Low** for the template's encryption-risk bullet."""

    if row_count < 50 or not fields:
        return "Low"
    hi = 0
    for fr in fields:
        if fr.inferred_semantic not in ("string", "mixed"):
            continue
        if fr.row_count <= 0:
            continue
        if fr.unique_pct >= 92.0 and fr.distinct_count > max(80, int(0.4 * fr.row_count)):
            hi += 1
    if hi >= max(2, len(fields) // 5):
        return "High"
    return "Low"


def build_automated_profiling_report_html(ctx: LoadedDatasetContext, rep: DatasetSummaryReport) -> str:
    """Return rich HTML for the five-section automated profiling template.

    Purpose
    -------
    Fill the user's executive-report outline using real metrics where available
    and explicit static text where the pipeline does not yet detect a signal.

    Internal Logic
    ----------------
    Pull file metadata from ``ctx.source_*``; pull tabular aggregates from ``rep``;
    call helpers for entropy, completeness, pooled numeric span, pattern scan,
    and risk heuristics; ``html.escape`` all file-derived names.

    Example invocation
    ------------------
    ``lbl.setText(build_automated_profiling_report_html(ctx, rep))``
    """

    path = ctx.source_data_path
    try:
        raw_size = path.stat().st_size
    except OSError:
        raw_size = -1
    size_txt = human_file_size(raw_size) if raw_size >= 0 else "unknown"
    file_name = html.escape(path.name)
    fmt_label = html.escape(_infer_file_format_label(path))
    delim_label = _delimiter_label(ctx.source_delimiter)

    fields = rep.fields
    rows = rep.duplicates.total_rows
    ncols = len(fields)
    dup_pct = rep.duplicates.duplicate_pct

    entropy = _entropy_score(fields, rows)
    primary = _primary_characteristic(fields, entropy, dup_pct)
    complete_pct, missing_cells = _completeness_and_missing(rep)
    n_num, n_txt, n_dt = _type_counts(fields)
    hi_name, hi_dist = _highest_cardinality_field(fields)
    gmin, gmax, gstd = _global_numeric_span(fields)
    patterns = _pattern_scan_total(str(ctx.database_path), ctx.table_name, fields)
    iqr_total = _total_iqr_outliers(fields)
    chrono = _chronological_sentence(fields)
    risk = _security_risk_label(fields, rows)

    density_line = (
        f"{rows:,} row(s) × {ncols} column(s) loaded into DuckDB; "
        f"source file on disk ≈ {size_txt}"
    )

    if gmin is not None and gmax is not None and gstd is not None:
        range_line = (
            f"Across numeric measure fields, pooled extrema run from a minimum of "
            f"<b>{gmin:g}</b> to a maximum of <b>{gmax:g}</b>; mean population "
            f"standard deviation across those fields is <b>{gstd:g}</b>."
        )
    elif gmin is not None and gmax is not None:
        range_line = (
            f"Across numeric measure fields, pooled extrema run from "
            f"<b>{gmin:g}</b> to <b>{gmax:g}</b> (standard deviation not aggregated)."
        )
    else:
        range_line = "No numeric measure columns were profiled for min/max/std.dev aggregation."

    parts: List[str] = [
        "<div style='font-weight:normal;'>",
        "<p><b>1. Executive Summary</b></p>",
        "<ul style='margin-top:0;'>",
        f"<li><b>File Name:</b> {file_name}</li>",
        f"<li><b>True File Type:</b> {fmt_label}</li>",
        f"<li><b>File Size:</b> {html.escape(size_txt)}</li>",
        f"<li><b>Data Density:</b> {html.escape(density_line)}</li>",
        "<li><b>Primary Characteristic:</b> This file contains highly "
        f"<b>{html.escape(primary)}</b> data with a total information entropy score "
        f"of <b>{entropy:g}</b> (normalized 0–10 diversity index).</li>",
        "</ul>",
        "<p><b>2. Structural &amp; Technical Properties</b></p>",
        "<ul style='margin-top:0;'>",
        "<li><b>Encoding Scheme:</b> UTF-8 (assumed for text import; not byte-sniffed).</li>",
        f"<li><b>Delimiters Detected:</b> {delim_label}</li>",
        f"<li><b>Data Dimension:</b> {ncols} distinct column(s) from metadata were loaded.</li>",
        "<li><b>Completeness:</b> The dataset is "
        f"<b>{complete_pct:.2f}%</b> complete (cell-level), with "
        f"<b>{missing_cells:,}</b> missing or blank string cell values counted across columns.</li>",
        "</ul>",
        "<p><b>3. Statistical Profile</b></p>",
        "<ul style='margin-top:0;'>",
        "<li><b>Data Type Distribution:</b> "
        f"<b>{n_num}</b> numeric-oriented field(s), <b>{n_txt}</b> text-oriented field(s), "
        f"<b>{n_dt}</b> date/time-oriented field(s) (inferred semantics).</li>",
        "<li><b>High-Cardinality Fields:</b> "
        f"<b>{html.escape(hi_name)}</b> contains the highest volume of distinct values "
        f"(<b>{hi_dist:,}</b>).</li>",
        f"<li><b>Value Range:</b> {range_line}</li>",
        "</ul>",
        "<p><b>4. Pattern &amp; Feature Recognition</b></p>",
        "<ul style='margin-top:0;'>",
        "<li><b>Identified Formats:</b> Automated regular expression scanning flagged "
        f"<b>{patterns:,}</b> cell matches for ISO-like dates, IPv4 literals, and email-shaped "
        "tokens (string columns only; capped scan).</li>",
        "<li><b>Duplication Rate:</b> "
        f"<b>{dup_pct:.2f}%</b> of rows are redundant identical full-row copies beyond the first "
        "occurrence of each distinct row fingerprint.</li>",
        f"<li><b>Chronological Markers:</b> {chrono}</li>",
        "</ul>",
        "<p><b>5. Anomalies &amp; Outliers</b></p>",
        "<ul style='margin-top:0;'>",
        "<li><b>Statistical Outliers:</b> "
        f"<b>{iqr_total:,}</b> numeric values fall outside Tukey 1.5×IQR fences across measure fields.</li>",
        "<li><b>Structural Breaks:</b> Not scanned — the loader treated each row as a uniform "
        "set of typed columns (no ragged line-length audit).</li>",
        "<li><b>Security/Encryption Risk:</b> "
        f"<b>{risk}</b> — heuristic from high-entropy string columns vs row count; "
        "not a substitute for malware or secrets scanning.</li>",
        "</ul>",
        "</div>",
    ]
    return "".join(parts)


__all__ = [
    "build_automated_profiling_report_html",
    "human_file_size",
]
