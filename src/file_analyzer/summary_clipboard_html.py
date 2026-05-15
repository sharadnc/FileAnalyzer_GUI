"""Full HTML export of the Summary tab for rich clipboard paste (Word, email, etc.).

Purpose
-------
:meth:`PyQt5.QtWidgets.QApplication.clipboard` accepts :class:`QMimeData` with
``text/html`` so recipients keep headings, tables, and inline emphasis that match
the on-screen Summary tab as closely as practical.

Internal Logic
----------------
- Reuse :func:`~file_analyzer.automated_profiling_report.build_automated_profiling_report_html`
  for the executive block.
- Mirror each :class:`~file_analyzer.summary_reports.FieldSummaryReport` with the
  same paragraphs and tables as :mod:`file_analyzer.ui.summary_tab`, including
  yellow highlight spans for key quality metrics.
- Emit a complete HTML5 document with a small embedded stylesheet for tables and
  section frames.

Example invocation
--------------------
``mime.setHtml(build_summary_clipboard_html_document(ctx, report))``
"""

from __future__ import annotations

import html
from typing import List

from file_analyzer.automated_profiling_report import build_automated_profiling_report_html
from file_analyzer.summary_reports import DatasetSummaryReport, FieldSummaryReport
from file_analyzer.ui.models import LoadedDatasetContext


def _span_bold_yellow(display: str, highlight: bool) -> str:
    """Return plain *display* or a bold yellow-highlighted span (same rules as the UI).

    Purpose
    -------
    Keep clipboard HTML aligned with Summary tab rules for nulls, blanks,
    missing density, and IQR counts greater than zero.

    Internal Logic
    ----------------
    Delegates to the same conditional span markup used in the Qt labels.

    Example invocation
    ------------------
    >>> _span_bold_yellow("1", True).startswith("<span")
    True
    """

    if not highlight:
        return html.escape(display)
    esc = html.escape(display)
    return (
        '<span style="font-weight: bold; background-color: #fff59d; padding: 0 2px; '
        f'border-radius: 2px;">{esc}</span>'
    )


def _html_data_table(headers: List[str], rows: List[List[str]]) -> str:
    """Render a data grid as an HTML table with bold header row and Total row styling."""

    head_cells = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body_parts: List[str] = []
    for row in rows:
        is_total = bool(row) and str(row[0]).strip().lower() == "total"
        row_style = " font-weight: bold;" if is_total else ""
        cells = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
        body_parts.append(f"<tr style='{row_style}'>{cells}</tr>")
    return (
        "<table style='border-collapse:collapse;width:100%;margin:8px 0;'>"
        f"<thead><tr style='background:#e8f4fc;'>{head_cells}</tr></thead>"
        f"<tbody>{''.join(body_parts)}</tbody></table>"
    )


def _field_block_html(fr: FieldSummaryReport) -> str:
    """Serialize one field summary to HTML matching the tab's ``_field_section``."""

    title_txt = f"{fr.field_name.upper()}  ({fr.field_dtype} · inferred: {fr.inferred_semantic})"
    title = html.escape(title_txt)
    type_line = html.escape(
        f"Meta type: {fr.meta_field_type} · DuckDB typeof(sample): {fr.duckdb_typeof_sample} · "
        f"High cardinality: {'yes' if fr.is_high_cardinality else 'no'}"
    )
    null_disp = _span_bold_yellow(str(fr.null_count), fr.null_count > 0)
    empty_disp = _span_bold_yellow(str(fr.empty_string_count), fr.empty_string_count > 0)
    miss_pct_disp = _span_bold_yellow(f"{fr.missing_pct:.2f}%", fr.missing_pct > 0.0)
    miss_inner = (
        f"Rows: {fr.row_count} · Nulls: {null_disp} · Empty/blank strings: {empty_disp} · "
        f"Missing density: {miss_pct_disp} · Distinct: {fr.distinct_count} · "
        f"Unique % of rows: {fr.unique_pct:.2f}%"
    )
    parts: List[str] = [
        '<div style="border:2px solid #3b7cb8;border-radius:6px;padding:12px;margin-top:14px;'
        'background-color:#f2f8fd;">',
        f"<h2 style='margin:0 0 8px 0;font-size:12pt;color:#0a2a4a;'>{title}</h2>",
        f"<p style='margin:6px 0;'>{type_line}</p>",
        f"<p style='margin:6px 0;'>{miss_inner}</p>",
    ]
    if fr.numeric_mean is not None or fr.numeric_median is not None:
        stats = html.escape(
            f"Min: {fr.numeric_min} · Max: {fr.numeric_max} · Mean: {fr.numeric_mean} · "
            f"Median: {fr.numeric_median} · Std.dev (pop): {fr.numeric_stddev_pop} · "
            f"Variance (pop): {fr.numeric_variance_pop}"
        )
        parts.append(f"<p style='margin:6px 0;'>{stats}</p>")
    if fr.outlier_count_iqr is not None and fr.outlier_pct is not None:
        oc = fr.outlier_count_iqr
        out_disp = _span_bold_yellow(str(oc), oc > 0)
        parts.append(
            "<p style='margin:6px 0;'>"
            f"IQR outliers (Tukey 1.5×IQR): {out_disp} rows "
            f"({fr.outlier_pct:.2f}% of non-null numeric values)</p>"
        )
    if fr.histogram_bins:
        cap = html.escape(
            "Value ranges (equal-width numeric bins; up to 50 densest bins, then Others; "
            "Total = all rows):"
        )
        parts.append(f"<p style='margin:6px 0;'>{cap}</p>")
        parts.append(_html_data_table(["Bin range", "Count"], [[a, str(b)] for a, b in fr.histogram_bins]))
    if fr.top_string_values:
        cap = html.escape(
            "Top values (string form; up to 50 values, then Others; Total = all rows):"
        )
        parts.append(f"<p style='margin:6px 0;'>{cap}</p>")
        parts.append(_html_data_table(["Value", "Count"], [[v, str(c)] for v, c in fr.top_string_values]))
    parts.append("</div>")
    return "".join(parts)


def build_summary_clipboard_html_document(ctx: LoadedDatasetContext, rep: DatasetSummaryReport) -> str:
    """Return a full HTML document mirroring the Summary tab for ``QMimeData.setHtml``.

    Purpose
    -------
    Provide one string suitable for ``text/html`` clipboard payloads so Word,
    Outlook, Google Docs, and similar consumers preserve structure.

    Internal Logic
    ----------------
    Concatenate document shell CSS, automated profiling (wrapped), dataset
    duplicate banner, intro paragraph, and each :meth:`_field_block_html` block.

    Example invocation
    ------------------
    After load, pass the same ``ctx`` and ``report`` used by the Summary tab::

        mime.setHtml(build_summary_clipboard_html_document(ctx, report))
    """

    dup = rep.duplicates
    banner_inner = (
        f"<b>Dataset</b> — rows: {dup.total_rows:,}; distinct full rows: {dup.distinct_full_rows:,}; "
        f"<b>duplicate extra rows</b> (identical full-row copies): {dup.duplicate_extra_rows:,} "
        f"({dup.duplicate_pct:.2f}%)"
    )
    intro = (
        "Per field: dimensions (D) are profiled as <b>string</b> values (not coerced to numeric); "
        "measures (M) show descriptive stats and up to 50 densest numeric bin ranges plus Others and a "
        "<b>Total</b> row matching row count. Missingness, IQR outliers, and duplicate tracking apply as before."
    )
    profiling = build_automated_profiling_report_html(ctx, rep)
    field_html = "".join(_field_block_html(fr) for fr in rep.fields)
    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset=\"utf-8\">"
        "<title>File Analyzer — Summary export</title>"
        "<style>"
        "body{font-family:Segoe UI,Arial,sans-serif;font-size:10pt;color:#111;margin:12px;max-width:960px;}"
        "h1{font-size:14pt;color:#0a2a4a;}"
        ".prof-wrap{border:2px solid #2d6a4e;border-radius:6px;padding:12px;background:#f4faf6;margin:0 0 16px;}"
        "</style></head><body>"
        "<h1>Summary export</h1>"
        f'<div class="prof-wrap">{profiling}</div>'
        f"<p>{banner_inner}</p>"
        f"<p>{intro}</p>"
        f"{field_html}"
        "</body></html>"
    )


__all__ = ["build_summary_clipboard_html_document"]
