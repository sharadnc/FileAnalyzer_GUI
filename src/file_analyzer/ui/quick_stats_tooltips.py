"""HTML quick-stats tooltips for field lists (Visualize, Pivot, and related UI).

Purpose
-------
Build the same hover tooltips used on the Visualize tab so other surfaces (Pivot
Rows/Columns/Values lists) can reuse them without importing :mod:`visualize_tab`
(which would create a circular import with :mod:`grid_tab`).

Internal Logic
---------------
1. Resolve :class:`~file_analyzer.stats_service.FieldQuickStats` per field from
   :class:`~file_analyzer.ui.models.LoadedDatasetContext`.
2. Render HTML tables (NULL row first, then frequencies or numeric summaries).
3. Expose :func:`dim_meas_source_rows_for_context` and
   :func:`quick_stats_tooltips_by_field_name` for list widgets.

Example invocation
--------------------
::

    tips = quick_stats_tooltips_by_field_name(ctx)
    item.setToolTip(tips["STATE"])
"""

from __future__ import annotations

from html import escape
from typing import Dict, Optional

from file_analyzer.meta_parser import FieldMeta, field_in_dimension_panels, field_in_measure_panels
from file_analyzer.stats_service import FieldQuickStats
from file_analyzer.ui.models import LoadedDatasetContext


def format_number_for_tooltip(value: float, decimal_places: int) -> str:
    """Format a numeric value for quick-stats HTML tooltips.

    Purpose
    -------
    Apply rounding and thousands separators using the load-time decimal setting.

    Example invocation
    --------------------
    ``format_number_for_tooltip(1234.5, 2)`` → ``\"1,234.50\"``.
    """

    if value != value:  # NaN check
        return "—"
    dp = max(0, min(30, int(decimal_places)))
    return f"{value:,.{dp}f}"


def quick_stats_null_count_row_html(null_count: int) -> str:
    """Return the first quick-stats table row: ``NULL`` count (always shown).

    Example invocation
    --------------------
    ``quick_stats_null_count_row_html(0)`` → HTML row with ``NULL`` / ``0``.
    """

    return (
        f"<tr><td style='text-align:left;padding:4px;'>NULL</td>"
        f"<td style='text-align:right;padding:4px;'>{int(null_count):,}</td></tr>"
    )


def build_tooltip_html(stats: FieldQuickStats, decimal_places: int) -> str:
    """Build rich HTML for a field hover tooltip (Visualize / Pivot field lists).

    Purpose
    -------
    Show ``FieldName [FieldDesc]``, mandatory ``NULL`` count, then type-specific
    stats in a mini-table with 4px cell padding.

    Example invocation
    --------------------
    ``html = build_tooltip_html(stats, ctx.measure_decimal_places)``
    """

    field = stats.field
    header = f"<b>{escape(field.name)}</b> [<i>{escape(field.description)}</i>]"
    null_row = quick_stats_null_count_row_html(stats.null_count)

    if stats.char_frequencies is not None:
        freq_rows = "".join(
            f"<tr>"
            f"<td style='text-align:left;padding:4px;'>{escape(str(value))}</td>"
            f"<td style='text-align:right;padding:4px;'>{cnt:,}</td>"
            f"</tr>"
            for value, cnt in stats.char_frequencies[:10]
        )
        return (
            f"<div style='padding:4px;'>{header}"
            f"<table style='border-collapse:collapse;'>{null_row}{freq_rows}</table>"
            f"</div>"
        )

    if stats.numeric_summary is not None:
        s = stats.numeric_summary
        body = null_row + (
            f"<tr><td style='text-align:left;padding:4px;'>Min</td>"
            f"<td style='text-align:right;padding:4px;'>"
            f"{format_number_for_tooltip(float(s['min']), decimal_places)}</td></tr>"
            f"<tr><td style='text-align:left;padding:4px;'>Max</td>"
            f"<td style='text-align:right;padding:4px;'>"
            f"{format_number_for_tooltip(float(s['max']), decimal_places)}</td></tr>"
            f"<tr><td style='text-align:left;padding:4px;'>Sum</td>"
            f"<td style='text-align:right;padding:4px;'>"
            f"{format_number_for_tooltip(float(s['sum']), decimal_places)}</td></tr>"
            f"<tr><td style='text-align:left;padding:4px;'>Mean</td>"
            f"<td style='text-align:right;padding:4px;'>"
            f"{format_number_for_tooltip(float(s['mean']), decimal_places)}</td></tr>"
            f"<tr><td style='text-align:left;padding:4px;'>Median</td>"
            f"<td style='text-align:right;padding:4px;'>"
            f"{format_number_for_tooltip(float(s['median']), decimal_places)}</td></tr>"
        )
        return (
            f"<div style='padding:4px;'>{header}"
            f"<table style='border-collapse:collapse;'>{body}</table>"
            f"</div>"
        )

    if stats.min_value is not None or stats.max_value is not None:
        minv = escape(str(stats.min_value)) if stats.min_value is not None else "—"
        maxv = escape(str(stats.max_value)) if stats.max_value is not None else "—"
        body = null_row + (
            f"<tr><td style='text-align:left;padding:4px;'>Min</td>"
            f"<td style='text-align:right;padding:4px;'>{minv}</td></tr>"
            f"<tr><td style='text-align:left;padding:4px;'>Max</td>"
            f"<td style='text-align:right;padding:4px;'>{maxv}</td></tr>"
        )
        return (
            f"<div style='padding:4px;'>{header}"
            f"<table style='border-collapse:collapse;'>{body}</table>"
            f"</div>"
        )

    return f"<div style='padding:4px;'>{header}</div>"


def field_list_tooltip_html(
    field: FieldMeta,
    stats: Optional[FieldQuickStats],
    decimal_places: int,
) -> str:
    """Build hover HTML for one field in a source or pivot field list.

    Purpose
    -------
    When quick stats exist, return :func:`build_tooltip_html`; otherwise show name,
    description, and a short unavailable note.

    Example invocation
    --------------------
    ``html = field_list_tooltip_html(field, ctx.quick_stats.get(name), 2)``
    """

    if stats is not None:
        return build_tooltip_html(stats, decimal_places)
    desc = (field.description or "").strip()
    inner = escape(desc) if desc else "<i>(no description)</i>"
    return (
        f"<div style='padding:4px;'><b>{escape(field.name)}</b> [<i>{inner}</i>]<br/>"
        "<small>Quick stats unavailable for this FieldType.</small></div>"
    )


def dim_meas_source_rows_for_context(
    ctx: LoadedDatasetContext,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Build ``(dimensions, measures)`` as ``(field_name, tooltip_html)`` pairs.

    Purpose
    -------
    Shared by Visualize source lists and Pivot Rows/Columns/Values lists.

    Example invocation
    --------------------
    ``dims, meas = dim_meas_source_rows_for_context(ctx)``
    """

    dims: list[tuple[str, str]] = []
    meas: list[tuple[str, str]] = []
    for field in ctx.meta.fields:
        stats = ctx.quick_stats.get(field.name)
        tip = field_list_tooltip_html(field, stats, ctx.measure_decimal_places)
        if field_in_dimension_panels(field):
            dims.append((field.name, tip))
        elif field_in_measure_panels(field):
            meas.append((field.name, tip))
    return dims, meas


def quick_stats_tooltips_by_field_name(ctx: LoadedDatasetContext) -> Dict[str, str]:
    """Map field name → quick-stats tooltip HTML for all panel-eligible fields.

    Purpose
    -------
    Populate Pivot Rows/Columns/Values ``QListWidget`` items with one lookup table.

    Example invocation
    --------------------
    ``tips = quick_stats_tooltips_by_field_name(ctx); item.setToolTip(tips[name])``
    """

    dims, meas = dim_meas_source_rows_for_context(ctx)
    return dict(dims + meas)
