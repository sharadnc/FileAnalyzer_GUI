"""Visualize tab UI skeleton for File Analyzer.

This file currently provides:
1. Dimension and Measure **source** lists list **every** metadata field (D/M);
   hover tooltips use quick stats when available and a short fallback otherwise.
2. **Multi-select** (Ctrl/Shift) and distinct selection styling on those lists.

The full charting experience (multi-select source lists, Plotly charts, chart↔table
interaction) is implemented in a later todo (`viz-plotly`). This module is
intentionally structured so later enhancements can extend these widgets rather
than replacing them.
"""

from __future__ import annotations

import base64
import json
import math
import shutil
import uuid
from html import escape
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from file_analyzer.config import load_app_config
from file_analyzer.meta_parser import (
    FieldMeta,
    field_displays_as_yyyymmdd,
    field_formats_as_measure,
    field_in_dimension_panels,
    field_in_measure_panels,
    format_yyyymmdd_display,
)
from file_analyzer.stats_service import FieldQuickStats
from file_analyzer.ui.grid_tab import _parse_measure_range_bounds
from file_analyzer.ui.models import LoadedDatasetContext
from file_analyzer.ui.quick_stats_tooltips import (
    build_tooltip_html as _build_tooltip_html,
    dim_meas_source_rows_for_context as _dim_meas_source_rows_for_context,
    field_list_tooltip_html as _visualize_field_list_tooltip,
    format_number_for_tooltip as _format_number,
    quick_stats_null_count_row_html as _quick_stats_null_count_row_html,
)

try:
    from PyQt5.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal, pyqtSlot
    from PyQt5.QtGui import QColor, QPainter, QPen
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QMessageBox,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    from PyQt5.QtWebChannel import QWebChannel
    from PyQt5.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView
except ModuleNotFoundError:  # pragma: no cover
    QObject = object  # type: ignore[assignment]
    Qt = object  # type: ignore[assignment]
    QThread = object  # type: ignore[assignment]
    QTimer = object  # type: ignore[assignment]
    QUrl = object  # type: ignore[assignment]
    pyqtSignal = object  # type: ignore[assignment]
    pyqtSlot = object  # type: ignore[assignment]
    QColor = object  # type: ignore[assignment]
    QPainter = object  # type: ignore[assignment]
    QPen = object  # type: ignore[assignment]
    QAbstractItemView = object  # type: ignore[assignment]
    QApplication = object  # type: ignore[assignment]
    QComboBox = object  # type: ignore[assignment]
    QFrame = object  # type: ignore[assignment]
    QHBoxLayout = object  # type: ignore[assignment]
    QLabel = object  # type: ignore[assignment]
    QLineEdit = object  # type: ignore[assignment]
    QListWidget = object  # type: ignore[assignment]
    QListWidgetItem = object  # type: ignore[assignment]
    QPushButton = object  # type: ignore[assignment]
    QMessageBox = object  # type: ignore[assignment]
    QScrollArea = object  # type: ignore[assignment]
    QSizePolicy = object  # type: ignore[assignment]
    QSplitter = object  # type: ignore[assignment]
    QTableWidget = object  # type: ignore[assignment]
    QTableWidgetItem = object  # type: ignore[assignment]
    QVBoxLayout = object  # type: ignore[assignment]
    QWidget = object  # type: ignore[assignment]
    QWebChannel = object  # type: ignore[assignment]
    QWebEngineSettings = object  # type: ignore[assignment]
    QWebEngineView = object  # type: ignore[assignment]


def _sql_ident(name: str) -> str:
    """Return a DuckDB double-quoted SQL identifier for a table or column name.

    Purpose
    -------
    Chart queries interpolate user-selected field names into SQL. Quoting avoids
    failures on reserved words (for example ``NAME``) and supports embedded quotes.

    Internal Logic
    ---------------
    Wrap the identifier in double quotes and escape any ``"`` as ``""``.

    Example invocation
    --------------------
    ``_sql_ident("NAME")`` → ``'"NAME"'`` (without the outer Python quotes, the
    fragment is: ``"NAME"``).
    """

    return '"' + str(name).replace('"', '""') + '"'


def _coerce_numeric_cell_for_chart_filter(value: object) -> Optional[float]:
    """Coerce a chart table cell to ``float`` for measure range comparisons.

    Purpose
    -------
    Chart rows store measures as numbers or strings. Range quick-filters (for
    example ``100-400``) compare inclusive bounds in float space, matching the
    Data Grid's ``try_cast(... AS DOUBLE) BETWEEN ...`` semantics.

    Internal Logic
    ---------------
    - ``None`` → ``None``.
    - ``int`` / ``float`` (excluding ``bool``) → ``float``; reject NaN/Inf.
    - Other values → strip, strip ASCII commas from thousands, parse ``float``;
      reject on ``ValueError`` or empty string.

    Example invocation
    --------------------
    ``_coerce_numeric_cell_for_chart_filter(1234.5)`` → ``1234.5``;
    ``_coerce_numeric_cell_for_chart_filter("1,234.5")`` → ``1234.5``;
    ``_coerce_numeric_cell_for_chart_filter("x")`` → ``None``.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    else:
        s = str(value).strip().replace(",", "")
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _build_chart_sort_order(
    user_sort: Sequence[Tuple[str, bool]],
    chart_columns: Sequence[str],
    file_key_columns: Sequence[str],
) -> list[Tuple[str, bool]]:
    """Merge user chart-table sort keys with primary-key tie-break columns.

    Purpose
    -------
    Chart tables often show a subset of fields. This builds the effective multi-key
    sort: user-chosen columns first, then any **file key** columns present in the
    chart (``ASC``) for a stable default matching SQL ``ORDER BY`` PK semantics.

    Internal Logic
    ---------------
    - Append each ``(col, asc)`` from ``user_sort`` when ``col`` is in
      ``chart_columns`` and not yet seen.
    - Append ``(pk, True)`` for each ``file_key_columns`` entry present in the
      chart but not yet seen (preserving meta PK order).
    - Append every remaining ``chart_columns`` field in display order as ``ASC``
      so all visible columns participate in tie-breaking.

    Example invocation
    --------------------
    ``_build_chart_sort_order([], ["SUMLEV", "NAME", "POP"], ["SUMLEV", "NAME"])`` →
    ``[("SUMLEV", True), ("NAME", True), ("POP", True)]``.
    """

    col_set = set(chart_columns)
    seen: set[str] = set()
    out: list[Tuple[str, bool]] = []
    for c, asc in user_sort:
        if c not in col_set or c in seen:
            continue
        out.append((c, asc))
        seen.add(c)
    for pk in file_key_columns:
        if pk in col_set and pk not in seen:
            out.append((pk, True))
            seen.add(pk)
    for c in chart_columns:
        if c not in seen:
            out.append((c, True))
            seen.add(c)
    return out


# Tableau 10 — colorblind-friendly fallback when Plotly color modules are unavailable.
_TABLEAU10_PALETTE: Tuple[str, ...] = (
    "#4E79A7",
    "#F28E2B",
    "#E15759",
    "#76B7B2",
    "#59A14F",
    "#EDC948",
    "#B07AA1",
    "#FF9DA7",
    "#9C755F",
    "#BAB0AC",
)


def _default_chart_palette() -> list[str]:
    """Return a discrete, colorblind-friendly palette for Plotly charts.

    Purpose
    -------
    Give bar, line, pie, stacked bar, histogram, and scatter traces distinguishable
    hues that work on the Visualize tab's light blue background.

    Internal Logic
    ---------------
    Prefer Plotly's built-in ``qualitative.Plotly`` and ``qualitative.Safe`` sequences
    (deduplicated, order preserved). Fall back to :data:`_TABLEAU10_PALETTE` when
    ``plotly`` is not importable. Callers cycle with ``i % len(palette)``.

    Example invocation
    --------------------
    ``pal = _default_chart_palette(); fig.update_layout(colorway=pal)``
    """

    try:
        import plotly.colors.qualitative as pq  # type: ignore[import-not-found]

        merged: list[str] = []
        seen: set[str] = set()
        for seq in (pq.Plotly, pq.Safe, pq.Bold):
            for hex_color in seq:
                if hex_color not in seen:
                    seen.add(hex_color)
                    merged.append(hex_color)
        if merged:
            return merged
    except Exception:
        pass
    return list(_TABLEAU10_PALETTE)


def _pie_chart_display_options(
    labels: Sequence[object],
    values: Sequence[object],
) -> Tuple[dict[str, object], dict[str, object]]:
    """Choose pie label density and layout margins so the chart stays readable.

    Purpose
    -------
    Show slice labels only when there is enough room; otherwise use hover and an
    optional legend. Tight margins enlarge the pie within the Plotly view.

    Internal Logic
    ----------------
    1. Coerce ``values`` to non-negative floats and compute slice count and
       minimum positive percent of total.
    2. Many slices (``>18``) or many thin slices → ``textinfo='none'`` + legend.
    3. Medium complexity → percent only with ``textposition='auto'`` and
       ``uniformtext.mode='hide'`` (Plotly drops text that would overlap).
    4. Few slices → ``label+percent`` outside labels with slightly larger margins.

    Example invocation
    --------------------
    ``trace_kw, layout_kw = _pie_chart_display_options([\"A\",\"B\"], [50, 50])``
    """

    floats: list[float] = []
    for v in values:
        try:
            floats.append(max(0.0, float(v)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            floats.append(0.0)
    n = len(floats)
    total = sum(floats) or 1.0
    positive_pcts = [(f / total) * 100.0 for f in floats if f > 0.0]
    min_pct = min(positive_pcts) if positive_pcts else 0.0

    trace: dict[str, object] = {
        "hole": 0,
        "sort": False,
        "direction": "clockwise",
        "automargin": True,
    }
    layout: dict[str, object] = {
        "margin": dict(l=2, r=2, t=28, b=2),
        "uniformtext": dict(minsize=10, mode="hide"),
        "showlegend": False,
    }

    if n == 0:
        trace["textinfo"] = "none"
        return trace, layout

    if n > 18 or (n > 10 and min_pct < 2.0):
        trace["textinfo"] = "none"
        layout["showlegend"] = True
        layout["legend"] = dict(
            orientation="v",
            yanchor="middle",
            y=0.5,
            xanchor="left",
            x=1.01,
            font=dict(size=10),
        )
        layout["margin"] = dict(l=2, r=140, t=28, b=2)
        return trace, layout

    if n > 12 or min_pct < 4.0:
        trace["textinfo"] = "percent"
        trace["textposition"] = "auto"
        trace["insidetextorientation"] = "horizontal"
        trace["textfont"] = dict(size=11, color="#1F1F1F")
        return trace, layout

    if n > 6:
        trace["textinfo"] = "label+percent"
        trace["textposition"] = "auto"
        trace["textfont"] = dict(size=11, color="#1F1F1F")
        return trace, layout

    trace["textinfo"] = "label+percent"
    trace["textposition"] = "outside"
    trace["textfont"] = dict(size=12, color="#1F1F1F")
    layout["margin"] = dict(l=6, r=6, t=32, b=6)
    return trace, layout


def _chart_dimension_click_equals(cell: object, target: object) -> bool:
    """Return whether a chart-table cell matches a Plotly click dimension value.

    Purpose
    -------
    Chart clicks send JSON primitives/strings while DuckDB rows may use numeric
    types, ``Decimal``, or differently cased strings. This predicate keeps
    ``NAME = United States`` style filtering reliable.

    Internal Logic
    ---------------
    - Both ``None`` or blank-target → match only if the cell is also empty.
    - Try finite ``float`` equality for both sides.
    - Fall back to ``str(...).strip().casefold()`` equality.

    Example invocation
    --------------------
    ``_chart_dimension_click_equals(1, "1")`` → ``True``;
    ``_chart_dimension_click_equals(" US ", "us")`` → ``True``.
    """

    def _is_blank(x: object) -> bool:
        return x is None or (isinstance(x, str) and x.strip() == "")

    if _is_blank(target):
        return _is_blank(cell)
    if cell is None:
        return False
    try:
        c = float(cell)  # type: ignore[arg-type]
        t = float(target)  # type: ignore[arg-type]
        if math.isfinite(c) and math.isfinite(t) and c == t:
            return True
    except (TypeError, ValueError):
        pass
    return str(cell).strip().casefold() == str(target).strip().casefold()


def _unwrap_plotly_click_value(val: object) -> object:
    """Flatten Plotly ``customdata`` fragments that are single-element list wrappers.

    Purpose
    -------
    Plotly sometimes delivers ``["United States"]`` or nested ``[["United States"]]``
    after JSON round-trips. Chart filtering compares the inner scalar.

    Internal Logic
    ---------------
    While ``val`` is a non-empty length-1 sequence, replace ``val`` with its sole
    element; otherwise return ``val``.

    Example invocation
    --------------------
    ``_unwrap_plotly_click_value([["US"]])`` → ``"US"``.
    """

    cur: object = val
    while isinstance(cur, (list, tuple)) and len(cur) == 1:
        cur = cur[0]
    return cur


def _plotly_click_dimension_token(cd: object) -> Optional[object]:
    """Return the first scalar dimension value from Plotly ``customdata`` JSON.

    Purpose
    -------
    Plotly may send ``["United States"]``, nested lists, or a bare string after
    ``JSON.parse``. Normalise to one Python value for row matching.

    Internal Logic
    ---------------
    If ``cd`` is a non-empty list, return the first element; if that element is a
    non-empty sequence, return its first element; otherwise return ``cd``.

    Example invocation
    --------------------
    ``_plotly_click_dimension_token([["US"]])`` → ``"US"``.
    """

    if isinstance(cd, list) and cd:
        first = cd[0]
        if isinstance(first, (list, tuple)) and first:
            return first[0]
        return first
    return cd if cd is not None else None


def filter_chart_rows_for_plotly_click(
    chart_type: str,
    chart_columns: list[str],
    ctx_dims: Sequence[str],
    original_rows: list[list[object]],
    customdata: object,
) -> list[list[object]]:
    """Return rows whose dimension column(s) match a Plotly click payload (pure logic).

    Purpose
    -------
    Unit-test chart↔table filtering without Qt or WebEngine. Mirrors
    :meth:`VisualizeTab._on_plotly_point_clicked` row selection rules.

    Internal Logic
    ---------------
    Same branches as the Visualize tab handler: unwrap tokens, resolve dimension
    column indices from ``ctx_dims`` and ``chart_columns``, compare with
    :func:`_chart_dimension_click_equals`.

    Example invocation
    --------------------
    ``filter_chart_rows_for_plotly_click("Bar", ["NAME", "POP"], ["NAME"], rows, ["United States"])``
    returns only rows where ``NAME`` matches ``United States``.
    """

    ctx_list = [str(d) for d in ctx_dims if d is not None and str(d).strip() != ""]

    def _dim_col_index(name: str, fallback: int) -> int:
        if name and name in chart_columns:
            return chart_columns.index(name)
        if 0 <= fallback < len(chart_columns):
            return fallback
        return 0

    if not customdata:
        return list(original_rows)
    if chart_type in ("Bar", "Line", "Pie", "Scatter"):
        target_raw = _plotly_click_dimension_token(customdata)
        target = _unwrap_plotly_click_value(target_raw) if target_raw is not None else None
        if target is None:
            return list(original_rows)
        dim_name = ctx_list[0] if ctx_list else ""
        di = _dim_col_index(dim_name, 0)
        out: list[list[object]] = []
        for row in original_rows:
            if di < len(row) and _chart_dimension_click_equals(row[di], target):
                out.append(row)
        return out
    if chart_type == "Stacked Bar":
        if not (isinstance(customdata, list) and len(customdata) >= 2):
            return list(original_rows)
        x_val = _unwrap_plotly_click_value(customdata[0])
        c_val = _unwrap_plotly_click_value(customdata[1])
        ix = _dim_col_index(ctx_list[0], 0) if len(ctx_list) > 0 else 0
        iy = _dim_col_index(ctx_list[1], 1) if len(ctx_list) > 1 else 1
        out = []
        for row in original_rows:
            if ix < len(row) and iy < len(row):
                if _chart_dimension_click_equals(row[ix], x_val) and _chart_dimension_click_equals(
                    row[iy], c_val
                ):
                    out.append(row)
        return out
    if chart_type == "Histogram":
        if not (isinstance(customdata, list) and len(customdata) >= 3):
            return list(original_rows)
        bin_idx = _unwrap_plotly_click_value(customdata[0])
        out = []
        for row in original_rows:
            if row and _chart_dimension_click_equals(row[0], bin_idx):
                out.append(row)
        return out
    return list(original_rows)


def _format_chart_filter_display_value(val: object) -> str:
    """Format one chart-click scalar for the Data Grid filter summary line.

    Purpose
    -------
    Match panel filter phrasing ``FIELD in (v1,v2)`` without SQL quoting.

    Internal Logic
    ---------------
    Coerce ``val`` with :func:`_unwrap_plotly_click_value`, then ``str`` for display.

    Example invocation
    --------------------
    ``_format_chart_filter_display_value([["Texas"]])`` → ``\"Texas\"``.
    """

    raw = _unwrap_plotly_click_value(val)
    if raw is None:
        return ""
    return str(raw).strip()


def build_chart_click_filter_human_summary(
    chart_type: str,
    *,
    dims: Sequence[str],
    measures: Sequence[str],
    customdata: object,
) -> str:
    """Build a panel-style summary for chart-linked grid filters.

    Purpose
    -------
    Produce text like ``NAME in (United States)`` or
    ``NAME in (US), REGION in (North)`` for the Data Grid **Filters -** line.

    Internal Logic
    ---------------
    Mirror :meth:`VisualizeTab._build_where_sql_from_click` branches: one dimension
    for bar/line/pie/scatter, two for stacked bar, measure range for histogram.

    Example invocation
    --------------------
    ``build_chart_click_filter_human_summary("Bar", dims=["NAME"], measures=["POP"],
    customdata=["United States"])`` → ``\"NAME in (United States)\"``.
    """

    dims_eff = [str(d).strip() for d in dims if d is not None and str(d).strip() != ""]
    meas_eff = [str(m).strip() for m in measures if m is not None and str(m).strip() != ""]

    if chart_type in ("Bar", "Line", "Pie", "Scatter"):
        if not dims_eff or customdata is None:
            return ""
        token_raw = _plotly_click_dimension_token(customdata)
        token = _format_chart_filter_display_value(token_raw) if token_raw is not None else ""
        if not token:
            return ""
        return f"{dims_eff[0]} in ({token})"

    if chart_type == "Stacked Bar":
        if len(dims_eff) < 2 or not (isinstance(customdata, list) and len(customdata) >= 2):
            return ""
        parts: list[str] = []
        for dim, raw in zip(dims_eff[:2], customdata[:2]):
            val = _format_chart_filter_display_value(raw)
            if val:
                parts.append(f"{dim} in ({val})")
        return ", ".join(parts)

    if chart_type == "Histogram":
        if not meas_eff or not (isinstance(customdata, list) and len(customdata) >= 3):
            return ""
        try:
            left = float(customdata[1])
            right = float(customdata[2])
        except (TypeError, ValueError):
            return ""
        return f"{meas_eff[0]} in ({left:g}-{right:g})"

    return ""


_SOURCE_LIST_STYLE = """
QListWidget {
    background-color: #FFFFFF;
}
QListWidget::item:selected {
    background-color: #7EC8E3;
    color: #000000;
}
QListWidget::item:selected:!active {
    background-color: #B8E6F5;
    color: #000000;
}
"""


def _configure_field_source_list(list_widget: QListWidget) -> None:
    """Enable multi-select and pastel selection styling on a dimension/measure list.

    Purpose
    -------
    Users pick several fields with Ctrl/Shift; selected rows use a distinct
    background so multi-selection is obvious before Generate.

    Internal Logic
    ---------------
    Set ``ExtendedSelection``, alternating row colors, and a small QSS block for
    ``:selected`` / ``:selected:!active`` item states.

    Example invocation
    --------------------
    ``_configure_field_source_list(self._dims_source)`` after populating items.
    """

    try:
        list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)  # type: ignore[attr-defined]
    except Exception:
        pass
    list_widget.setAlternatingRowColors(True)
    try:
        list_widget.setStyleSheet(_SOURCE_LIST_STYLE)
    except Exception:
        pass




def _resolve_plotly_min_js_path() -> Path:
    """Locate Plotly's bundled ``plotly.min.js`` inside the installed ``plotly`` package.

    Purpose
    -------
    ``fig.to_html(include_plotlyjs=True)`` injects large inline script/CSS that older
    Qt WebEngine builds mishandle (``Plotly is not defined``, ``insertRule`` errors).
    Loading this file via ``<script src=\"plotly.min.js\">`` next to the chart HTML
    keeps execution order reliable.

    Internal Logic
    ---------------
    Search ``package_data`` next to ``plotly.__file__`` for ``plotly.min.js`` then
    ``plotly.js``.

    Example invocation
    --------------------
    ``shutil.copy2(_resolve_plotly_min_js_path(), dest_dir / \"plotly.min.js\")``
    """

    import plotly  # type: ignore[import-not-found]

    root = Path(plotly.__file__).resolve().parent
    for name in ("plotly.min.js", "plotly.js"):
        candidate = root / "package_data" / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No plotly.min.js under {root / 'package_data'}")


class _ChartSpinnerWidget(QWidget):
    """Small rotating arc spinner for the chart loading overlay.

    Purpose
    -------
    Show a standard busy indicator while DuckDB/Plotly work runs without bundling
    an external GIF asset.

    Internal Logic
    ----------------
    A ``QTimer`` advances :attr:`_angle` every 80ms; :meth:`paintEvent` draws a
    partial arc with theme blue (#0F6CBD).

    Example invocation
    --------------------
    ``spinner = _ChartSpinnerWidget(); spinner.start()``
    """

    def __init__(self, parent: Optional[QWidget] = None, *, size: int = 48) -> None:
        super().__init__(parent)
        self._angle = 0
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)  # type: ignore[attr-defined]

    def _tick(self) -> None:
        """Advance rotation and repaint."""

        self._angle = (self._angle + 30) % 360
        self.update()

    def start(self) -> None:
        """Start the rotation animation."""

        self._timer.start()
        self.show()

    def stop(self) -> None:
        """Stop the rotation animation."""

        self._timer.stop()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Draw the spinner arc."""

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)  # type: ignore[attr-defined]
        pen = QPen(QColor("#0F6CBD"))
        pen.setWidth(4)
        try:
            pen.setCapStyle(Qt.RoundCap)  # type: ignore[attr-defined]
        except Exception:
            pass
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)  # type: ignore[attr-defined]
        inset = 4
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        painter.drawArc(rect, int(self._angle * 16), int(270 * 16))
        painter.end()


class _ChartLoadingOverlay(QFrame):
    """Semi-transparent overlay with spinner and status text over the Plotly view."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background-color: rgba(247, 251, 255, 0.88); border: none; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addStretch(1)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        self._spinner = _ChartSpinnerWidget(center, size=52)
        center_layout.addWidget(self._spinner, 0, Qt.AlignHCenter)  # type: ignore[attr-defined]
        self._message = QLabel("Generating visualization…")
        try:
            self._message.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)  # type: ignore[attr-defined]
            self._message.setStyleSheet("color: #1F1F1F; font-size: 11pt; font-weight: 600;")
        except Exception:
            pass
        center_layout.addWidget(self._message, 0, Qt.AlignHCenter)  # type: ignore[attr-defined]
        layout.addWidget(center, 0, Qt.AlignHCenter)  # type: ignore[attr-defined]
        layout.addStretch(1)
        self.hide()

    def start(self) -> None:
        """Show overlay and run the spinner."""

        self._spinner.start()
        self.show()
        self.raise_()

    def stop(self) -> None:
        """Hide overlay and stop the spinner."""

        self._spinner.stop()
        self.hide()


class _ChartPlotContainer(QWidget):
    """Hosts :class:`QWebEngineView` with a loading overlay that tracks resize."""

    def __init__(self, plot_view: QWebEngineView, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._plot_view = plot_view
        self._plot_view.setParent(self)
        self._overlay = _ChartLoadingOverlay(self)

    def plot_view(self) -> QWebEngineView:
        """Return the embedded Plotly web view."""

        return self._plot_view

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Keep plot and overlay geometry aligned with this container."""

        super().resizeEvent(event)
        self._plot_view.setGeometry(0, 0, self.width(), self.height())
        self._overlay.setGeometry(0, 0, self.width(), self.height())

    def show_loading(self) -> None:
        """Display the generating overlay on top of the chart."""

        self._overlay.start()

    def hide_loading(self) -> None:
        """Hide the generating overlay."""

        self._overlay.stop()


class VisualizeTab(QWidget):
    """Visualize tab UI with Plotly and an interactive chart table.

    Purpose
    -------
    Let users **multi-select** Dimension/Measure fields in source lists, generate
    a Plotly chart, and inspect the underlying chart table below the chart.

    Internal Logic
    ---------------
    - Source lists (Dimensions/Measures) are populated from metadata.
    - Users **multi-select** fields (Ctrl/Shift) in each list; highlighted rows use
      a distinct background. **Generate Visualization** uses list order: the first
      selected measure drives the Y-axis; the first selected dimension(s) drive X
      (the first two dimensions for Stacked Bar).
    - A chart type dropdown + “Generate Visualization” builds:
      - a Plotly chart (rendered in :class:`QWebEngineView`),
      - a chart table (QTableWidget) under the chart with **sortable headers**
        (click / Ctrl+multi-sort / Shift reset to primary-key order) and
      - per-column quick search filter boxes for the chart table.
    - Client-side **pagination** (same default page size as the Data Grid) slices the
      sorted, quick-filtered chart table rows.
    - Clicking a chart mark filters the chart table and pushes a WHERE clause
      to the Data Grid and Pivot Data tab sinks (when bound).
    """

    def __init__(self, ctx: LoadedDatasetContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._grid_sink = None
        self._pivot_sink = None
        self._config = load_app_config()

        # Current chart dataset shown in the chart table.
        self._chart_columns: list[str] = []
        self._chart_rows: list[list[object]] = []
        self._chart_original_rows: list[list[object]] = []

        # Rows quick-filters scan (last ``_rebuild_chart_table_from_rows`` input: full or click subset).
        self._chart_filter_source_rows: list[list[object]] = []

        # Chart table header sort (in-memory); empty user list => PK columns in chart only.
        self._chart_sort_specs: list[tuple[str, bool]] = []

        # Chart table pagination (in-memory; matches Data Grid default page size).
        self._chart_page_size: int = int(self._config.page_size_default)
        self._chart_page_index: int = 0
        self._chart_all_display_rows: list[list[object]] = []

        # Column filters for the chart table (aligned strip + dict for lookup).
        self._chart_search_inputs: dict[str, QLineEdit] = {}
        self._chart_filter_scroll: Optional[QScrollArea] = None
        self._chart_filter_inner: Optional[QWidget] = None
        self._chart_filter_layout: Optional[QHBoxLayout] = None
        self._chart_filter_corner: Optional[QWidget] = None
        self._chart_filter_line_edits: list[QLineEdit] = []
        self._chart_filter_wired: bool = False
        self._CHART_FILTER_STRIP_ROW: int = 28

        # Chart-click payload context.
        self._last_click_ctx: dict[str, object] = {}

        self._chart_thread: Optional[QThread] = None
        self._chart_worker: Optional[QObject] = None

        self._last_chart_html_path: Optional[Path] = None
        self._plotly_vendor_js_ready: bool = False
        self._chart_plot_container: Optional[_ChartPlotContainer] = None
        self._chart_load_pending: bool = False

        self._build_ui()
        try:
            self._plot_view.loadFinished.connect(self._on_chart_web_load_finished)  # type: ignore[attr-defined]
        except Exception:
            pass

    def bind_grid_tab(self, grid_tab) -> None:
        """Bind the Data Grid tab sink used for chart-click synchronization."""

        self._grid_sink = grid_tab

    def bind_pivot_tab(self, pivot_tab) -> None:
        """Bind the Pivot Data tab sink used for chart-click synchronization."""

        self._pivot_sink = pivot_tab

    def wait_for_background_threads(self) -> None:
        """Block until chart ``QThread`` finishes (used on main window close).

        Purpose
        -------
        Prevent destroying a running chart worker ``QThread`` when the user closes
        the application during chart generation.

        Internal Logic
        ----------------
        If ``_chart_thread`` exists and ``isRunning``, call :meth:`QThread.wait`.

        Example invocation
        --------------------
        Invoked from the main window ``closeEvent`` before widgets are destroyed.
        """

        t = self._chart_thread
        if t is not None and t.isRunning():
            t.wait()

    def _build_ui(self) -> None:
        """Create and lay out all widgets in this tab."""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(
            QLabel(
                "Select Dimensions and Measures (Ctrl/Shift for multi-select), "
                "then click Generate Visualization."
            )
        )

        # Source lists (horizontally resizable panes).
        source_dims_wrap = QWidget()
        source_dims_layout = QVBoxLayout(source_dims_wrap)
        source_dims_layout.setContentsMargins(0, 0, 0, 0)
        source_dims_layout.addWidget(QLabel("Dimensions (source)"))
        self._dims_source = QListWidget()
        source_dims_layout.addWidget(self._dims_source, 1)

        source_meas_wrap = QWidget()
        source_meas_layout = QVBoxLayout(source_meas_wrap)
        source_meas_layout.setContentsMargins(0, 0, 0, 0)
        source_meas_layout.addWidget(QLabel("Measures (source)"))
        self._meas_source = QListWidget()
        source_meas_layout.addWidget(self._meas_source, 1)

        self._lists_splitter = QSplitter(Qt.Horizontal)
        self._lists_splitter.setChildrenCollapsible(False)
        self._lists_splitter.addWidget(source_dims_wrap)
        self._lists_splitter.addWidget(source_meas_wrap)
        self._lists_splitter.setStretchFactor(0, 1)
        self._lists_splitter.setStretchFactor(1, 1)
        self._lists_splitter.setMinimumHeight(140)

        dim_rows, meas_rows = _dim_meas_source_rows_for_context(self._ctx)
        for name, tooltip in dim_rows:
            self._dims_source.addItem(name)
            self._dims_source.item(self._dims_source.count() - 1).setToolTip(tooltip)
        for name, tooltip in meas_rows:
            self._meas_source.addItem(name)
            self._meas_source.item(self._meas_source.count() - 1).setToolTip(tooltip)

        _configure_field_source_list(self._dims_source)
        _configure_field_source_list(self._meas_source)

        select_hint = QLabel(
            "Tip: list order defines chart axes — first selected measure on Y; "
            "first dimension on X (first two dimensions for Stacked Bar)."
        )
        select_hint.setWordWrap(True)

        try:
            self._dims_source.itemSelectionChanged.connect(self._sync_submit_enabled)  # type: ignore[attr-defined]
            self._meas_source.itemSelectionChanged.connect(self._sync_submit_enabled)  # type: ignore[attr-defined]
        except Exception:
            pass

        # Chart controls.
        controls_row = QHBoxLayout()

        controls_row.addWidget(QLabel("Chart type:"))
        self._chart_type_combo = QComboBox()
        self._chart_type_combo.addItems(["Line", "Pie", "Bar", "Stacked Bar", "Histogram", "Scatter"])
        self._chart_type_combo.setCurrentIndex(2)
        self._chart_type_combo.setEnabled(True)
        controls_row.addWidget(self._chart_type_combo, 1)

        self._submit_chart_btn = QPushButton("Generate Visualization")
        self._submit_chart_btn.setEnabled(False)
        controls_row.addWidget(self._submit_chart_btn, 0)

        self._clear_chart_selection_btn = QPushButton("Clear chart selection")
        self._clear_chart_selection_btn.setEnabled(True)
        controls_row.addWidget(self._clear_chart_selection_btn, 0)

        self._submit_chart_btn.clicked.connect(self._on_generate_clicked)  # type: ignore[attr-defined]
        self._clear_chart_selection_btn.clicked.connect(self._on_clear_selection)  # type: ignore[attr-defined]
        try:
            self._chart_type_combo.currentIndexChanged.connect(self._sync_submit_enabled)  # type: ignore[attr-defined]
        except Exception:
            pass

        # Upper block (lists + controls) vs chart area: vertically resizable.
        upper_block = QWidget()
        upper_layout = QVBoxLayout(upper_block)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.addWidget(self._lists_splitter, 0)
        upper_layout.addWidget(select_hint, 0)
        upper_layout.addLayout(controls_row, 0)

        # Chart + table (vertically resizable).
        self._chart_table_splitter = QSplitter(Qt.Vertical)
        self._chart_table_splitter.setChildrenCollapsible(False)

        self._plot_view = QWebEngineView()
        try:
            ws = self._plot_view.settings()
            ws.setAttribute(QWebEngineSettings.JavascriptEnabled, True)  # type: ignore[attr-defined]
            ws.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)  # type: ignore[attr-defined]
            # Allow ``file:`` chart pages to load ``qrc:///qtwebchannel/qwebchannel.js`` for clicks.
            ws.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._plot_view.setMinimumHeight(220)
        self._chart_plot_container = _ChartPlotContainer(self._plot_view)
        self._chart_plot_container.setMinimumHeight(220)
        self._chart_table_splitter.addWidget(self._chart_plot_container)

        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)

        self._chart_filter_scroll = QScrollArea()
        self._chart_filter_scroll.setFrameShape(QFrame.NoFrame)  # type: ignore[attr-defined]
        self._chart_filter_scroll.setWidgetResizable(False)
        try:
            self._chart_filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # type: ignore[attr-defined]
            self._chart_filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # type: ignore[attr-defined]
            self._chart_filter_scroll.setSizePolicy(
                QSizePolicy.Expanding,  # type: ignore[attr-defined]
                QSizePolicy.Fixed,  # type: ignore[attr-defined]
            )
        except Exception:
            pass
        self._chart_filter_inner = QWidget()
        self._chart_filter_inner.setFixedHeight(self._CHART_FILTER_STRIP_ROW)
        self._chart_filter_layout = QHBoxLayout(self._chart_filter_inner)
        self._chart_filter_layout.setContentsMargins(0, 0, 0, 0)
        self._chart_filter_layout.setSpacing(0)
        self._chart_filter_corner = QWidget()
        self._chart_filter_corner.setFixedSize(1, self._CHART_FILTER_STRIP_ROW)
        self._chart_filter_layout.addWidget(self._chart_filter_corner, 0)
        self._chart_filter_scroll.setWidget(self._chart_filter_inner)
        self._chart_filter_scroll.setFixedHeight(self._CHART_FILTER_STRIP_ROW + 2)
        self._chart_filter_scroll.setStyleSheet(
            "QScrollArea { background: #E8F2FB; border-bottom: 1px solid #86BDEB; }"
        )
        table_layout.addWidget(self._chart_filter_scroll, 0)

        self._chart_table = QTableWidget()
        self._chart_table.setColumnCount(0)
        self._chart_table.setRowCount(0)
        self._chart_table.setStyleSheet(
            """
            QTableWidget {
                gridline-color: #C7D9EC;
                background: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #D7ECFF;
                color: #000000;
                font-weight: bold;
                padding: 5px 8px;
                border: 1px solid #86BDEB;
            }
            """
        )
        table_layout.addWidget(self._chart_table, 1)

        chart_pager_row = QHBoxLayout()
        self._chart_prev_btn = QPushButton("Prev")
        self._chart_next_btn = QPushButton("Next")
        self._chart_page_label = QLabel("")
        chart_pager_row.addWidget(self._chart_prev_btn, 0)
        chart_pager_row.addWidget(self._chart_page_label, 1)
        chart_pager_row.addWidget(self._chart_next_btn, 0)
        pager_widget = QWidget()
        pager_widget.setLayout(chart_pager_row)
        table_layout.addWidget(pager_widget, 0)
        self._chart_prev_btn.clicked.connect(self._on_chart_table_prev_page)  # type: ignore[attr-defined]
        self._chart_next_btn.clicked.connect(self._on_chart_table_next_page)  # type: ignore[attr-defined]

        try:
            chdr = self._chart_table.horizontalHeader()
            chdr.sectionClicked.connect(self._on_chart_header_sort_clicked)  # type: ignore[attr-defined]
            chdr.setToolTip(
                "Click: sort by column (toggle ↑/↓). Ctrl+click: add or toggle multi-sort. "
                "Shift+click: reset to primary-key order. Arrows show the active sort."
            )
        except Exception:
            pass

        hint = QLabel("Click a chart mark to filter the chart table.")
        hint.setVisible(True)
        table_layout.addWidget(hint)

        self._chart_table_splitter.addWidget(table_container)
        self._chart_table_splitter.setStretchFactor(0, 1)
        self._chart_table_splitter.setStretchFactor(1, 1)

        self._main_splitter = QSplitter(Qt.Vertical)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(upper_block)
        self._main_splitter.addWidget(self._chart_table_splitter)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setSizes([220, 620])
        self._chart_table_splitter.setSizes([420, 240])
        layout.addWidget(self._main_splitter, 1)

        self._lists_splitter.setObjectName("fa_layout_viz_lists")
        self._chart_table_splitter.setObjectName("fa_layout_viz_chart_table")
        self._main_splitter.setObjectName("fa_layout_viz_main")
        try:
            from PyQt5.QtCore import QSettings  # type: ignore[import-not-found]

            from file_analyzer.ui.layout_persistence import (
                restore_splitter_state,
                wire_splitter_autosave,
            )

            _lay = QSettings()
            restore_splitter_state(_lay, self._lists_splitter, self._lists_splitter.objectName())
            restore_splitter_state(_lay, self._chart_table_splitter, self._chart_table_splitter.objectName())
            restore_splitter_state(_lay, self._main_splitter, self._main_splitter.objectName())
            wire_splitter_autosave(self._lists_splitter, self._lists_splitter.objectName(), self)
            wire_splitter_autosave(self._chart_table_splitter, self._chart_table_splitter.objectName(), self)
            wire_splitter_autosave(self._main_splitter, self._main_splitter.objectName(), self)
        except Exception:
            pass

        self._sync_submit_enabled()

    def _selected_dimensions(self) -> list[str]:
        """Return selected dimension field names in visual list order (top to bottom)."""

        out: list[str] = []
        for i in range(self._dims_source.count()):
            it = self._dims_source.item(i)
            if it.isSelected():
                out.append(it.text())
        return out

    def _selected_measures(self) -> list[str]:
        """Return selected measure field names in visual list order (top to bottom)."""

        out: list[str] = []
        for i in range(self._meas_source.count()):
            it = self._meas_source.item(i)
            if it.isSelected():
                out.append(it.text())
        return out

    def _sync_submit_enabled(self) -> None:
        """Enable Generate when the current chart type has enough selections."""

        measures = self._selected_measures()
        dims = self._selected_dimensions()
        chart_type = self._chart_type_combo.currentText()

        if not measures:
            self._submit_chart_btn.setEnabled(False)
            return
        if chart_type == "Histogram":
            self._submit_chart_btn.setEnabled(True)
            return
        if chart_type == "Stacked Bar":
            self._submit_chart_btn.setEnabled(len(dims) >= 2)
            return
        if chart_type in ("Bar", "Line", "Pie", "Scatter"):
            self._submit_chart_btn.setEnabled(len(dims) >= 1)
            return
        self._submit_chart_btn.setEnabled(True)

    def export_reload_session_state(self) -> Dict[str, object]:
        """Capture chart type and dimension/measure list selections for reload.

        Purpose
        -------
        After ``Load Data`` rebuilds tabs, :meth:`import_reload_session_state` can
        restore the Visualize shelf the user had before a zoom-driven reload.

        Internal Logic
        ----------------
        Store chart combo index and selected field names in list order for both
        source lists.

        Example invocation
        --------------------
        ``snap[\"visualize\"] = visualize_tab.export_reload_session_state()``
        """

        def selected_in_list_order(lst: QListWidget) -> list[str]:
            out: list[str] = []
            for i in range(lst.count()):
                it = lst.item(i)
                if it.isSelected():
                    out.append(it.text())
            return out

        return {
            "chart_type_index": int(self._chart_type_combo.currentIndex()),
            "dims": selected_in_list_order(self._dims_source),
            "measures": selected_in_list_order(self._meas_source),
        }

    def import_reload_session_state(self, data: Optional[Dict[str, object]]) -> None:
        """Restore chart type and list selections from :meth:`export_reload_session_state`."""

        if not data or not isinstance(data, dict):
            return
        try:
            idx = int(data.get("chart_type_index", self._chart_type_combo.currentIndex()))
            idx = max(0, min(self._chart_type_combo.count() - 1, idx))
            self._chart_type_combo.setCurrentIndex(idx)
        except Exception:
            pass
        dims = data.get("dims")
        meas = data.get("measures")
        if isinstance(dims, (list, tuple)):
            self._restore_list_selections(self._dims_source, [str(x) for x in dims])
        if isinstance(meas, (list, tuple)):
            self._restore_list_selections(self._meas_source, [str(x) for x in meas])
        self._sync_submit_enabled()

    def _restore_list_selections(self, lst: QListWidget, names: Sequence[str]) -> None:
        """Select items whose text appears in ``names`` (others cleared)."""

        want = set(names)
        lst.blockSignals(True)
        lst.clearSelection()
        for i in range(lst.count()):
            it = lst.item(i)
            if it.text() in want:
                it.setSelected(True)
        lst.blockSignals(False)

    def _on_clear_selection(self) -> None:
        """Clear chart table filters and grid sink filter."""

        self._rebuild_chart_table_from_rows(self._chart_original_rows)
        self._sync_chart_filter_geometry()
        if self._grid_sink is not None:
            try:
                self._grid_sink.set_chart_click_filter("")
            except Exception:
                pass
        if self._pivot_sink is not None:
            try:
                self._pivot_sink.set_chart_click_filter("")
            except Exception:
                pass

    def _on_generate_clicked(self) -> None:
        """Generate the Plotly chart and chart table."""

        dims = self._selected_dimensions()
        measures = self._selected_measures()
        chart_type = self._chart_type_combo.currentText()

        if not measures:
            QMessageBox.warning(
                self,
                "Chart requirements",
                "Select at least one measure in the Measures (source) list (Ctrl/Shift for multi-select).",
            )
            return
        if chart_type == "Stacked Bar" and len(dims) < 2:
            QMessageBox.warning(
                self,
                "Chart requirements",
                "Stacked Bar needs at least two dimensions selected (list order: first on X, second for stack color).",
            )
            return
        if chart_type in ("Bar", "Stacked Bar", "Line", "Pie", "Scatter") and not dims:
            QMessageBox.warning(
                self,
                "Chart requirements",
                f"“{chart_type}” needs at least one dimension selected in the Dimensions (source) list.",
            )
            return

        self._submit_chart_btn.setEnabled(False)
        self._show_chart_loading()

        if self._chart_thread is not None and self._chart_thread.isRunning():
            self._chart_thread.wait()
        self._chart_thread = None
        self._chart_worker = None

        class _BuildChartWorker(QObject):
            finished = pyqtSignal(object)
            failed = pyqtSignal(str)

            def __init__(self_nonlocal, ctx: LoadedDatasetContext, host_thread: QThread) -> None:
                super().__init__()
                self_nonlocal._ctx = ctx
                self_nonlocal._host_thread = host_thread

            @pyqtSlot()
            def run(self_nonlocal) -> None:
                """DuckDB query + prepare table rows for chart rendering."""

                try:
                    import duckdb  # type: ignore
                    import numpy as np  # type: ignore

                    db_path = self_nonlocal._ctx.database_path
                    if db_path is None:
                        raise RuntimeError("Dataset database path is missing; cannot build chart.")

                    conn = duckdb.connect(database=str(db_path))
                    tbl = _sql_ident(self_nonlocal._ctx.table_name)

                    if chart_type == "Histogram":
                        y_col = measures[0]
                        y_q = _sql_ident(y_col)
                        min_val, max_val = conn.execute(
                            f"SELECT MIN({y_q}), MAX({y_q}) FROM {tbl}"
                        ).fetchone()
                        if min_val is None or max_val is None:
                            raise RuntimeError("Histogram requires MIN/MAX values.")

                        minf = float(min_val)
                        maxf = float(max_val)
                        if minf == maxf:
                            raise RuntimeError("Histogram requires a non-zero numeric range.")

                        # 10 equal-width bins -> 11 edges.
                        edges = np.linspace(minf, maxf, 11)
                        bin_start_col = "bin_min"
                        bin_end_col = "bin_max"

                        cases = []
                        for i in range(len(edges) - 1):
                            left = edges[i]
                            right = edges[i + 1]
                            if i == len(edges) - 2:
                                cases.append(
                                    f"WHEN {y_q} >= {left} AND {y_q} <= {right} THEN {i}"
                                )
                            else:
                                cases.append(
                                    f"WHEN {y_q} >= {left} AND {y_q} < {right} THEN {i}"
                                )

                        case_sql = " ".join(cases)
                        sql = f"""
                            SELECT
                                bin_idx,
                                COUNT(*) AS cnt,
                                {edges[0]}::DOUBLE + (bin_idx * 1)::DOUBLE * 0 AS {bin_start_col},
                                {edges[0]}::DOUBLE + ((bin_idx + 1) * 1)::DOUBLE * 0 AS {bin_end_col}
                            FROM (
                                SELECT
                                    CASE {case_sql} ELSE NULL END AS bin_idx,
                                    {y_q}
                                FROM {tbl}
                            ) t
                            WHERE bin_idx IS NOT NULL
                            GROUP BY bin_idx
                            ORDER BY bin_idx;
                        """
                        # Above uses placeholders for bin min/max; compute precisely in Python.
                        bin_rows = conn.execute(sql).fetchall()
                        conn.close()

                        columns = ["bin_idx", "cnt", bin_start_col, bin_end_col]
                        rows = []
                        for bin_idx, cnt, _s, _e in bin_rows:
                            idx = int(bin_idx)
                            rows.append([idx, int(cnt), float(edges[idx]), float(edges[idx + 1])])

                        self_nonlocal.finished.emit(
                            {
                                "chart_type": chart_type,
                                "columns": columns,
                                "rows": rows,
                                "dims": [],
                                "measures": measures,
                                "measure_col": y_col,
                                "click_mode": "histogram",
                            }
                        )
                        return

                    # Default bar/line/pie/scatter aggregation (multi-measure where supported).
                    dim_x = dims[0] if dims else None
                    if dim_x is None:
                        raise RuntimeError("A Dimension is required for this chart type.")

                    if chart_type == "Stacked Bar":
                        if len(dims) < 2:
                            raise RuntimeError("Stacked Bar requires at least 2 Dimensions.")
                        dim_color = dims[1]
                        dx = _sql_ident(dim_x)
                        dc = _sql_ident(dim_color)
                        y_q = _sql_ident(measures[0])
                        sql = f"""
                            SELECT {dx}, {dc}, SUM({y_q}) AS agg_value
                            FROM {tbl}
                            GROUP BY {dx}, {dc}
                            ORDER BY {dx}, {dc};
                        """
                        columns = [dim_x, dim_color, "agg_value"]
                        rows = conn.execute(sql).fetchall()
                        conn.close()
                        self_nonlocal.finished.emit(
                            {
                                "chart_type": chart_type,
                                "columns": columns,
                                "rows": rows,
                                "dims": dims,
                                "measures": measures,
                                "measure_col": measures[0],
                                "click_mode": "stacked_bar",
                            }
                        )
                        return

                    if chart_type in ("Bar", "Line"):
                        dx = _sql_ident(dim_x)
                        sum_parts = ", ".join(
                            f"SUM({_sql_ident(m)}) AS {_sql_ident(m)}" for m in measures
                        )
                        sql = f"""
                            SELECT {dx}, {sum_parts}
                            FROM {tbl}
                            GROUP BY {dx}
                            ORDER BY {dx};
                        """
                        columns = [dim_x] + list(measures)
                        rows = conn.execute(sql).fetchall()
                        conn.close()
                        self_nonlocal.finished.emit(
                            {
                                "chart_type": chart_type,
                                "columns": columns,
                                "rows": rows,
                                "dims": dims,
                                "measures": measures,
                                "measure_col": measures[0],
                                "click_mode": "single_dim",
                            }
                        )
                        return

                    if chart_type == "Pie":
                        m0 = measures[0]
                        mq = _sql_ident(m0)
                        dx = _sql_ident(dim_x)
                        sql = f"""
                            SELECT {dx}, SUM({mq}) AS {mq}
                            FROM {tbl}
                            GROUP BY {dx}
                            ORDER BY {mq} DESC;
                        """
                        columns = [dim_x, m0]
                        rows = conn.execute(sql).fetchall()
                        conn.close()
                        self_nonlocal.finished.emit(
                            {
                                "chart_type": chart_type,
                                "columns": columns,
                                "rows": rows,
                                "dims": dims,
                                "measures": measures,
                                "measure_col": m0,
                                "click_mode": "single_dim",
                            }
                        )
                        return

                    if chart_type == "Scatter":
                        dx = _sql_ident(dim_x)
                        avg_parts = ", ".join(
                            f"AVG({_sql_ident(m)}) AS {_sql_ident(m)}" for m in measures
                        )
                        sql = f"""
                            SELECT {dx}, {avg_parts}
                            FROM {tbl}
                            GROUP BY {dx}
                            ORDER BY {dx};
                        """
                        columns = [dim_x] + list(measures)
                        rows = conn.execute(sql).fetchall()
                        conn.close()
                        self_nonlocal.finished.emit(
                            {
                                "chart_type": chart_type,
                                "columns": columns,
                                "rows": rows,
                                "dims": dims,
                                "measures": measures,
                                "measure_col": measures[0],
                                "click_mode": "single_dim",
                            }
                        )
                        return

                    conn.close()
                    self_nonlocal.failed.emit("Unsupported chart type.")
                except Exception as e:
                    self_nonlocal.failed.emit(str(e))
                finally:
                    self_nonlocal._host_thread.quit()

        thread = QThread()
        worker = _BuildChartWorker(self._ctx, thread)
        self._chart_thread = thread
        self._chart_worker = worker
        worker.moveToThread(thread)

        def on_finished(result: object) -> None:
            thread.wait()
            self._chart_thread = None
            self._chart_worker = None
            try:
                if not isinstance(result, dict):
                    raise TypeError(f"Unexpected chart worker payload type: {type(result)!r}")
                self._render_chart_and_table(result)
            except Exception as exc:
                self._chart_load_pending = False
                self._hide_chart_loading()
                self._submit_chart_btn.setEnabled(True)
                QMessageBox.critical(self, "Chart render failed", str(exc))

        def on_failed(msg: str) -> None:
            thread.wait()
            self._chart_thread = None
            self._chart_worker = None
            self._chart_load_pending = False
            self._hide_chart_loading()
            self._submit_chart_btn.setEnabled(True)
            # Chart table remains usable on failure.
            self._chart_filter_scroll.setEnabled(True)
            self._plot_view.setHtml(f"<html><body><pre>{msg}</pre></body></html>")

        worker.finished.connect(on_finished)  # type: ignore[attr-defined]
        worker.failed.connect(on_failed)  # type: ignore[attr-defined]
        thread.started.connect(worker.run)  # type: ignore[attr-defined]
        thread.start()

    def _render_chart_and_table(self, result: dict) -> None:
        """Render the Plotly chart and populate the chart table."""

        self._last_click_ctx = result
        chart_type = str(result.get("chart_type", ""))
        cols = list(result.get("columns", []))
        rows = [list(r) for r in result.get("rows", [])]

        self._chart_columns = cols
        self._chart_rows = rows
        self._chart_original_rows = list(rows)
        self._chart_sort_specs = []

        self._setup_chart_column_filters()
        self._rebuild_chart_table_from_rows(rows)
        try:
            self._chart_table.resizeColumnsToContents()
            for c in range(self._chart_table.columnCount()):
                cur = self._chart_table.columnWidth(c)
                self._chart_table.setColumnWidth(c, min(max(cur, 70), 220))
        except Exception:
            pass
        self._sync_chart_filter_geometry()
        self._wire_chart_table_filter_signals()
        try:
            QTimer.singleShot(0, self._sync_chart_filter_geometry)  # type: ignore[attr-defined]
        except Exception:
            pass

        import plotly.graph_objects as go  # type: ignore

        self._last_pie_layout_opts: Optional[dict[str, object]] = None
        chart_palette = _default_chart_palette()
        n_palette = len(chart_palette)

        fig = None
        if chart_type in ("Bar", "Line"):
            n_m = len(cols) - 1
            x_values = [r[0] for r in rows] if rows else []
            fig = go.Figure()
            if n_m > 0 and rows:
                for j in range(n_m):
                    mname = cols[j + 1]
                    y_vals = [r[j + 1] for r in rows]
                    customdata = [[str(xv)] for xv in x_values]
                    trace_clr = chart_palette[j % n_palette]
                    if chart_type == "Line":
                        if n_m == 1:
                            # One measure: vary marker color along X; keep a single line accent.
                            pt_colors = [chart_palette[i % n_palette] for i in range(len(x_values))]
                            fig.add_trace(
                                go.Scatter(
                                    x=x_values,
                                    y=y_vals,
                                    mode="lines+markers",
                                    name=mname,
                                    customdata=customdata,
                                    line=dict(color=trace_clr, width=2),
                                    marker=dict(color=pt_colors, size=9),
                                )
                            )
                        else:
                            fig.add_trace(
                                go.Scatter(
                                    x=x_values,
                                    y=y_vals,
                                    mode="lines+markers",
                                    name=mname,
                                    customdata=customdata,
                                    line=dict(color=trace_clr),
                                    marker=dict(color=trace_clr),
                                )
                            )
                    else:
                        if n_m == 1:
                            bar_colors = [chart_palette[i % n_palette] for i in range(len(x_values))]
                            fig.add_trace(
                                go.Bar(
                                    x=x_values,
                                    y=y_vals,
                                    name=mname,
                                    customdata=customdata,
                                    marker=dict(color=bar_colors),
                                )
                            )
                        else:
                            fig.add_trace(
                                go.Bar(
                                    x=x_values,
                                    y=y_vals,
                                    name=mname,
                                    customdata=customdata,
                                    marker=dict(color=trace_clr),
                                )
                            )
                if chart_type == "Bar" and n_m > 1:
                    fig.update_layout(barmode="group")
                if n_m > 1:
                    fig.update_layout(
                        showlegend=True,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    )
        elif chart_type == "Pie":
            x_values = [r[0] for r in rows]
            y_values = [r[1] for r in rows]
            customdata = [[str(xv)] for xv in x_values]
            colors = [chart_palette[i % n_palette] for i in range(len(x_values))]
            pie_trace_opts, pie_layout_opts = _pie_chart_display_options(x_values, y_values)
            fig = go.Figure(
                data=go.Pie(
                    labels=x_values,
                    values=y_values,
                    customdata=customdata,
                    marker=dict(colors=colors, line=dict(color="#FFFFFF", width=1)),
                    hovertemplate="%{label}<br>%{value:,.2f}<br>%{percent}<extra></extra>",
                    **pie_trace_opts,
                )
            )
            self._last_pie_layout_opts = pie_layout_opts
        elif chart_type == "Stacked Bar":
            x_col = cols[0]
            color_col = cols[1]
            x_values = [r[0] for r in rows]
            color_values = [r[1] for r in rows]
            agg_values = [r[2] for r in rows]

            fig = go.Figure()
            unique_colors = sorted(set(color_values), key=lambda v: str(v))
            for idx, cval in enumerate(unique_colors):
                trace_x = [x_values[i] for i in range(len(x_values)) if color_values[i] == cval]
                trace_y = [agg_values[i] for i in range(len(agg_values)) if color_values[i] == cval]
                trace_customdata = [[str(trace_x[i]), str(cval)] for i in range(len(trace_x))]
                fig.add_trace(
                    go.Bar(
                        name=str(cval),
                        x=trace_x,
                        y=trace_y,
                        customdata=trace_customdata,
                        marker=dict(color=chart_palette[idx % n_palette]),
                    )
                )
            fig.update_layout(barmode="stack")
        elif chart_type == "Histogram":
            # columns: ["bin_idx", "cnt", "bin_min", "bin_max"]
            dp = max(0, min(30, int(self._ctx.measure_decimal_places)))
            x_values = [f"{float(r[2]):,.{dp}f}-{float(r[3]):,.{dp}f}" for r in rows]
            y_values = [r[1] for r in rows]
            # Include bin_idx so click filtering does not depend on float string equality.
            customdata = [[int(r[0]), float(r[2]), float(r[3])] for r in rows]
            hist_colors = [chart_palette[i % n_palette] for i in range(len(rows))]
            fig = go.Figure(
                data=go.Bar(x=x_values, y=y_values, customdata=customdata, marker=dict(color=hist_colors))
            )
        elif chart_type == "Scatter":
            n_m = len(cols) - 1
            x_values = [r[0] for r in rows] if rows else []
            fig = go.Figure()
            if n_m > 0 and rows:
                for j in range(n_m):
                    mname = cols[j + 1]
                    y_vals = [r[j + 1] for r in rows]
                    customdata = [[str(xv)] for xv in x_values]
                    trace_clr = chart_palette[j % n_palette]
                    if n_m == 1:
                        pt_colors = [chart_palette[i % n_palette] for i in range(len(x_values))]
                        fig.add_trace(
                            go.Scatter(
                                x=x_values,
                                y=y_vals,
                                mode="markers",
                                name=mname,
                                marker=dict(color=pt_colors, size=9),
                                customdata=customdata,
                            )
                        )
                    else:
                        fig.add_trace(
                            go.Scatter(
                                x=x_values,
                                y=y_vals,
                                mode="markers",
                                name=mname,
                                marker=dict(color=trace_clr, size=9),
                                customdata=customdata,
                            )
                        )
                if n_m > 1:
                    fig.update_layout(
                        showlegend=True,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    )
        else:
            fig = go.Figure()

        layout_kwargs: dict[str, object] = dict(
            template="plotly_white",
            colorway=chart_palette,
            paper_bgcolor="#F7FBFF",
            plot_bgcolor="#F7FBFF",
            font=dict(color="#1F1F1F", family="Segoe UI, Arial, sans-serif"),
            legend=dict(font=dict(size=11)),
        )
        if self._last_pie_layout_opts is not None:
            layout_kwargs.update(self._last_pie_layout_opts)
        fig.update_layout(**layout_kwargs)

        self._plotly_bridge = _PlotlyBridge(self)
        channel = QWebChannel(self._plot_view.page())
        channel.registerObject("bridge", self._plotly_bridge)
        self._plot_view.page().setWebChannel(channel)

        html = self._plotly_figure_to_html(fig, channel_name="bridge")
        self._write_chart_html_and_load(html)

    def _write_chart_html_and_load(self, html: str) -> None:
        """Persist Plotly HTML and open it via ``file:`` URL in ``QWebEngineView``.

        Purpose
        -------
        ``QWebEngineView.setHtml`` can truncate or fail silently on large documents;
        Plotly's default ``to_html(include_plotlyjs=True)`` output is often several MB.
        Loading the same markup from a UTF-8 file avoids that limit so the chart renders.

        Internal Logic
        ---------------
        Delete the previous chart HTML path if present, write ``html`` under
        :attr:`LoadedDatasetContext.temp_dir` with a unique file name, then call
        :meth:`QWebEngineView.setUrl` with :meth:`QUrl.fromLocalFile`. Fall back to
        ``setHtml`` if ``setUrl`` raises.

        Example invocation
        --------------------
        Called at the end of :meth:`_render_chart_and_table` after building ``html``.
        """

        prev = self._last_chart_html_path
        if prev is not None:
            try:
                prev.unlink(missing_ok=True)
            except OSError:
                pass
        out = self._ctx.temp_dir / f"viz_chart_{uuid.uuid4().hex}.html"
        out.write_text(html, encoding="utf-8")
        self._last_chart_html_path = out
        self._chart_load_pending = True
        try:
            self._plot_view.setUrl(QUrl.fromLocalFile(str(out.resolve())))  # type: ignore[attr-defined]
        except Exception:
            self._plot_view.setHtml(html)
            QTimer.singleShot(0, self._finish_chart_loading_ui)  # type: ignore[attr-defined]

    def _show_chart_loading(self) -> None:
        """Show the spinner overlay on the Plotly chart area."""

        if self._chart_plot_container is not None:
            self._chart_plot_container.show_loading()

    def _hide_chart_loading(self) -> None:
        """Hide the spinner overlay on the Plotly chart area."""

        if self._chart_plot_container is not None:
            self._chart_plot_container.hide_loading()

    def _finish_chart_loading_ui(self) -> None:
        """Re-enable controls and hide the loading overlay after chart display."""

        self._chart_load_pending = False
        self._hide_chart_loading()
        self._submit_chart_btn.setEnabled(True)

    def _on_chart_web_load_finished(self, ok: bool) -> None:
        """Hide loading overlay when the Plotly HTML page finishes loading in WebEngine."""

        if self._chart_load_pending:
            self._finish_chart_loading_ui()
        elif not ok:
            self._finish_chart_loading_ui()

    def _sync_plotly_vendor_js(self) -> None:
        """Copy bundled ``plotly.min.js`` beside session chart HTML (once per tab)."""

        if self._plotly_vendor_js_ready:
            return
        src = _resolve_plotly_min_js_path()
        dst = self._ctx.temp_dir / "plotly.min.js"
        shutil.copy2(src, dst)
        self._plotly_vendor_js_ready = True

    def _plotly_figure_to_html(self, fig, channel_name: str) -> str:
        """Build HTML that loads local ``plotly.min.js`` then ``Plotly.newPlot`` from JSON.

        Purpose
        -------
        Avoid ``fig.to_html(include_plotlyjs=True)``, which breaks on Qt WebEngine
        (``Plotly is not defined``, CSS ``insertRule`` / ``:focus-visible`` errors).
        A relative ``<script src=\"plotly.min.js\">`` plus a base64 JSON bundle keeps
        load order deterministic. Qt WebEngine's older Chromium rejects Plotly's
        ``:focus-visible`` stylesheet rules unless ``insertRule`` failures are caught.

        Internal Logic
        ---------------
        Inject a tiny shim that wraps ``CSSStyleSheet.prototype.insertRule`` to swallow
        parse errors, then load ``plotly.min.js``. Call :meth:`_sync_plotly_vendor_js`,
        serialize ``fig.to_plotly_json()`` with :class:`plotly.utils.PlotlyJSONEncoder`,
        base64-embed the JSON, decode in JS, call ``Plotly.newPlot('fa_plot', ...)``,
        then attach click handlers.

        Example invocation
        --------------------
        ``html = self._plotly_figure_to_html(fig, channel_name=\"bridge\")``
        """

        self._sync_plotly_vendor_js()
        try:
            from plotly.utils import PlotlyJSONEncoder  # type: ignore[import-not-found]
        except Exception:  # pragma: no cover
            PlotlyJSONEncoder = None  # type: ignore[misc,assignment]

        bundle = fig.to_plotly_json()
        payload = {
            "data": bundle.get("data", []),
            "layout": bundle.get("layout", {}),
            "config": bundle.get("config", {}),
        }
        if PlotlyJSONEncoder is not None:
            raw = json.dumps(payload, cls=PlotlyJSONEncoder, separators=(",", ":"))
        else:
            raw = json.dumps(payload, default=str, separators=(",", ":"))
        b64 = base64.b64encode(raw.encode("utf-8")).decode("ascii")

        return f"""
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <script>
      (function() {{
        try {{
          if (window.CSSStyleSheet && CSSStyleSheet.prototype.insertRule) {{
            var __faInsertRule = CSSStyleSheet.prototype.insertRule;
            CSSStyleSheet.prototype.insertRule = function(rule, index) {{
              try {{
                return __faInsertRule.call(this, rule, index);
              }} catch (e) {{
                try {{
                  return this.cssRules.length;
                }} catch (e2) {{
                  return 0;
                }}
              }}
            }};
          }}
        }} catch (e) {{}}
      }})();
    </script>
    <script src="plotly.min.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
      html, body {{ margin: 0; padding: 0; height: 100%; background: #F7FBFF; }}
      #fa_plot {{ width: 100%; height: 100vh; min-height: 320px; }}
    </style>
  </head>
  <body>
    <div id="fa_plot"></div>
    <script>
      var __b64 = '{b64}';
      var __faBundle = JSON.parse(atob(__b64));
      var DATA = __faBundle.data;
      var LAYOUT = __faBundle.layout || {{}};
      var CONFIG = __faBundle.config || {{}};

      var __faPyBridge = null;

      function initQWebChannel(done) {{
        if (__faPyBridge) {{
          if (done) done();
          return;
        }}
        if (typeof QWebChannel === 'undefined') {{
          setTimeout(function() {{ initQWebChannel(done); }}, 40);
          return;
        }}
        if (typeof qt === 'undefined' || !qt.webChannelTransport) {{
          setTimeout(function() {{ initQWebChannel(done); }}, 40);
          return;
        }}
        try {{
          new QWebChannel(qt.webChannelTransport, function(channel) {{
            __faPyBridge = channel.objects.{channel_name} || null;
            if (done) done();
          }});
        }} catch (e) {{
          setTimeout(function() {{ initQWebChannel(done); }}, 80);
        }}
      }}

      function safeCall(payload) {{
        try {{
          function send() {{
            var obj = __faPyBridge;
            if (!obj) return;
            if (obj.onPlotlyClick) obj.onPlotlyClick(payload);
            else if (obj.on_plotly_click) obj.on_plotly_click(payload);
            else if (obj.on_plotlyclick) obj.on_plotlyclick(payload);
          }}
          if (__faPyBridge) send();
          else initQWebChannel(send);
        }} catch (e) {{}}
      }}

      function plotlyClickPayload(data) {{
        try {{
          var pts = (data && data.points) ? data.points : [];
          if (pts.length === 0) return null;
          var p0 = pts[0];
          var cd = p0.customdata;
          if (cd === undefined || cd === null) {{
            if (p0.label !== undefined && p0.label !== null)
              return JSON.stringify([String(p0.label)]);
            if (p0.x !== undefined && p0.x !== null) return JSON.stringify([String(p0.x)]);
            return null;
          }}
          if (!Array.isArray(cd)) cd = [cd];
          return JSON.stringify(cd);
        }} catch (e) {{ return null; }}
      }}

      function bindClicks() {{
        var gd = document.getElementById('fa_plot');
        if (!gd) return;
        if (gd.__fa_click_bound__) return;
        gd.__fa_click_bound__ = true;
        var handler = function(evOrData) {{
          try {{
            var data = evOrData;
            if (evOrData && evOrData.detail && typeof evOrData.detail === 'object' && evOrData.detail.points) {{
              data = evOrData.detail;
            }}
            var pl = plotlyClickPayload(data);
            if (pl) safeCall(pl);
          }} catch (e) {{}}
        }};
        if (typeof gd.on === 'function') {{
          gd.on('plotly_click', handler);
        }} else {{
          gd.addEventListener('plotly_click', function(ev) {{ handler(ev); }});
        }}
      }}

      function scheduleBinds() {{
        initQWebChannel(function() {{
          setTimeout(bindClicks, 50);
          setTimeout(bindClicks, 500);
        }});
      }}

      function renderPlot() {{
        var tries = 0;
        function attempt() {{
          if (typeof Plotly === 'undefined') {{
            tries++;
            if (tries < 40) {{
              setTimeout(attempt, 50);
              return;
            }}
            var el = document.getElementById('fa_plot');
            if (el) {{
              el.innerHTML = '<pre style="padding:8px">Plotly did not load. Check that plotly.min.js is in the same folder as this HTML file.</pre>';
            }}
            return;
          }}
          var p = Plotly.newPlot('fa_plot', DATA, LAYOUT, CONFIG);
          if (p && typeof p.then === 'function') {{
            p.then(scheduleBinds).catch(scheduleBinds);
          }} else {{
            scheduleBinds();
          }}
        }}
        attempt();
      }}

      if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', renderPlot);
      }} else {{
        renderPlot();
      }}
    </script>
  </body>
</html>
"""

    def _chart_sort_column_order(self) -> list[tuple[str, bool]]:
        """Return effective multi-column sort keys (user order, then PK tie-break).

        Purpose
        -------
        Chart SQL already grouped data; the table re-sorts in memory for UX.
        This list mirrors the Data Grid rule: user keys first, then file keys
        present in :attr:`_chart_columns`.

        Internal Logic
        ---------------
        Delegate to :func:`_build_chart_sort_order` using current specs and meta.

        Example invocation
        --------------------
        Called from :meth:`_chart_sort_key_tuple` for each ``sorted`` comparison.
        """

        return _build_chart_sort_order(
            self._chart_sort_specs,
            self._chart_columns,
            self._ctx.meta.file_key_columns,
        )

    def _chart_sort_tuple_component(self, val: object, col: str, asc: bool) -> object:
        """Build one comparable fragment for a single cell (measure vs dimension).

        Purpose
        -------
        ``sorted`` needs a tuple key; measures use float space (nulls last), strings
        use case-folded text with a UTF-8 code-unit inversion trick for ``DESC``.

        Internal Logic
        ---------------
        - Measures: :func:`_coerce_numeric_cell_for_chart_filter`; invalid → tier 2.
        - Ascending numbers use ``(0, n)``; descending use ``(0, -n)``.
        - Dimensions: ``(0, s)`` ascending; descending ``(0, tuple(-b for b in utf8))``.

        Example invocation
        --------------------
        ``_chart_sort_tuple_component(3.5, "POP", True)`` → ``(0, 3.5)``.
        """

        if self._is_measure_column(col):
            n = _coerce_numeric_cell_for_chart_filter(val)
            if n is None or (isinstance(n, float) and (math.isnan(n) or math.isinf(n))):
                return (2, 0.0)
            v = n if asc else -n
            return (0, v)
        s = "" if val is None else str(val).casefold()
        if asc:
            return (0, s)
        encoded = s.encode("utf-8", errors="ignore")
        inv = tuple(-int(b) for b in encoded)
        return (0, inv)

    def _chart_sort_key_tuple(self, row: list[object]) -> tuple[object, ...]:
        """Build the full tuple sort key for one chart row."""

        order = self._chart_sort_column_order()
        col_to_idx = {c: i for i, c in enumerate(self._chart_columns)}
        parts: list[object] = []
        for col, asc in order:
            idx = col_to_idx.get(col, -1)
            cell = row[idx] if 0 <= idx < len(row) else None
            parts.append(self._chart_sort_tuple_component(cell, col, asc))
        return tuple(parts)

    def _sort_chart_rows_in_memory(self, rows: list[list[object]]) -> list[list[object]]:
        """Return a sorted copy of chart rows using the active header sort keys."""

        if not rows or not self._chart_columns:
            return list(rows)
        return sorted(rows, key=self._chart_sort_key_tuple)

    def _apply_chart_header_labels_with_sort(self) -> None:
        """Set chart horizontal header items with ↑/↓ markers and ``UserRole`` names.

        Purpose
        -------
        Match the Data Grid pattern: arrows show sort direction; superscript ²…⁹
        marks secondary sort keys. ``Qt.UserRole`` stores the raw field name.

        Internal Logic
        ---------------
        Same label rules as the Data Grid tab header row.

        Example invocation
        --------------------
        Called from :meth:`_rebuild_chart_table_from_rows` after ``setColumnCount``.
        """

        sup_after_first: dict[int, str] = {
            2: "\u00B2",
            3: "\u00B3",
            4: "\u2074",
            5: "\u2075",
            6: "\u2076",
            7: "\u2077",
            8: "\u2078",
            9: "\u2079",
        }
        for i, col in enumerate(self._chart_columns):
            base = col.upper()
            label = base
            for pos, (name, asc) in enumerate(self._chart_sort_specs):
                if name != col:
                    continue
                arrow = "\u2191" if asc else "\u2193"
                if pos == 0:
                    label = f"{base} {arrow}"
                else:
                    sup = sup_after_first.get(pos + 1, f"({pos + 1})")
                    label = f"{base} {arrow}{sup}"
                break
            item = QTableWidgetItem(label)
            item.setData(Qt.UserRole, col)  # type: ignore[attr-defined]
            self._chart_table.setHorizontalHeaderItem(i, item)

    def _on_chart_header_sort_clicked(self, logical_index: int) -> None:
        """Handle chart-table header clicks (sort, Ctrl multi-sort, Shift reset)."""

        if not self._chart_columns:
            return
        if logical_index < 0 or logical_index >= len(self._chart_columns):
            return
        col = self._chart_columns[logical_index]

        try:
            mods = QApplication.keyboardModifiers()  # type: ignore[attr-defined]
        except Exception:
            mods = Qt.NoModifier  # type: ignore[attr-defined]

        try:
            m_int = int(mods)  # type: ignore[arg-type]
            shift = int(Qt.ShiftModifier)  # type: ignore[attr-defined]
            ctrl = int(Qt.ControlModifier) | int(Qt.MetaModifier)  # type: ignore[attr-defined]
        except Exception:
            m_int = 0
            shift = 0
            ctrl = 0

        if m_int & shift:
            self._chart_sort_specs = []
            self._rebuild_chart_table_from_rows(self._chart_original_rows)
            self._sync_chart_filter_geometry()
            return

        specs = list(self._chart_sort_specs)
        if m_int & ctrl:
            found = -1
            for i, (name, _a) in enumerate(specs):
                if name == col:
                    found = i
                    break
            if found >= 0:
                n, a = specs[found]
                specs[found] = (n, not a)
            else:
                specs.append((col, True))
            self._chart_sort_specs = specs
        else:
            if len(specs) == 1 and specs[0][0] == col:
                n, a = specs[0]
                self._chart_sort_specs = [(n, not a)]
            else:
                self._chart_sort_specs = [(col, True)]

        self._rebuild_chart_table_from_rows(self._chart_original_rows)
        self._sync_chart_filter_geometry()

    def _collect_quick_filtered_chart_rows(self, base_sorted: list[list[object]]) -> list[list[object]]:
        """Return ``base_sorted`` rows that pass all non-empty chart quick-filter edits.

        Purpose
        -------
        Shared by pagination recompute and filter refresh so sort + substring/range
        rules stay consistent.

        Internal Logic
        ---------------
        If there are no filter widgets, return a shallow copy of ``base_sorted``.
        Otherwise mirror :meth:`_apply_column_filters` predicate logic row-wise.

        Example invocation
        --------------------
        ``self._collect_quick_filtered_chart_rows(sorted_rows)`` before paging.
        """

        if not self._chart_search_inputs:
            return list(base_sorted)
        filtered: list[list[object]] = []
        for row in base_sorted:
            keep = True
            for c_idx, col in enumerate(self._chart_columns):
                inp = self._chart_search_inputs.get(col)
                if inp is None:
                    continue
                raw = inp.text().strip()
                if not raw:
                    continue
                cell_val = row[c_idx]
                if self._is_measure_column(col):
                    mbounds = _parse_measure_range_bounds(raw)
                    if mbounds is not None:
                        lo, hi = mbounds
                        num = _coerce_numeric_cell_for_chart_filter(cell_val)
                        if num is None or num < lo or num > hi:
                            keep = False
                            break
                        continue
                q = raw.lower()
                cell = "" if cell_val is None else str(cell_val).lower()
                if q not in cell:
                    keep = False
                    break
            if keep:
                filtered.append(row)
        return filtered

    def _recompute_chart_all_display_rows(self) -> None:
        """Sort + quick-filter into :attr:`_chart_all_display_rows` and reset to page 0."""

        base_sorted = self._sort_chart_rows_in_memory(list(self._chart_filter_source_rows))
        self._chart_all_display_rows = self._collect_quick_filtered_chart_rows(base_sorted)
        self._chart_page_index = 0

    def _update_chart_table_page_label(self) -> None:
        """Refresh the chart-table pager label (page index, total rows)."""

        total = len(self._chart_all_display_rows)
        ps = max(1, int(self._chart_page_size))
        pages = max(1, math.ceil(total / ps)) if total else 1
        self._chart_page_label.setText(f"Page {self._chart_page_index + 1} / {pages} (Rows: {total})")

    def _populate_current_chart_table_page(self) -> None:
        """Show the current page slice in the chart ``QTableWidget``."""

        total = len(self._chart_all_display_rows)
        ps = max(1, int(self._chart_page_size))
        max_page = max(0, math.ceil(total / ps) - 1) if total else 0
        if self._chart_page_index > max_page:
            self._chart_page_index = max_page
        start = self._chart_page_index * ps
        end = min(start + ps, total)
        page_rows = self._chart_all_display_rows[start:end]
        self._apply_chart_header_labels_with_sort()
        self._populate_chart_table_rows(page_rows)
        self._update_chart_table_page_label()

    def _on_chart_table_prev_page(self) -> None:
        """Go to the previous chart-table page."""

        if self._chart_page_index > 0:
            self._chart_page_index -= 1
            self._populate_current_chart_table_page()

    def _on_chart_table_next_page(self) -> None:
        """Go to the next chart-table page."""

        total = len(self._chart_all_display_rows)
        ps = max(1, int(self._chart_page_size))
        max_page = max(0, math.ceil(total / ps) - 1) if total else 0
        if self._chart_page_index < max_page:
            self._chart_page_index += 1
            self._populate_current_chart_table_page()

    def _rebuild_chart_table_from_rows(self, rows: list[list[object]]) -> None:
        """Rebuild chart table structure, recompute display rows, and show page 1."""

        self._chart_filter_source_rows = list(rows)
        self._chart_table.setRowCount(0)
        self._chart_table.setColumnCount(len(self._chart_columns))
        self._recompute_chart_all_display_rows()
        self._populate_current_chart_table_page()

    def _populate_chart_table_rows(self, rows: list[list[object]]) -> None:
        """Populate the QTableWidget with row data."""

        self._chart_table.setRowCount(len(rows))
        for r_idx, row in enumerate(rows):
            for c_idx, value in enumerate(row):
                text = self._format_cell_value(value, self._chart_columns[c_idx])
                item = QTableWidgetItem(text)
                if r_idx % 2 == 1:
                    item.setBackground(QColor("#EDF6FF"))

                if self._is_measure_column(self._chart_columns[c_idx]):
                    item.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
                else:
                    item.setTextAlignment(int(Qt.AlignLeft | Qt.AlignVCenter))

                self._chart_table.setItem(r_idx, c_idx, item)

    def _format_cell_value(self, value: object, column_name: str) -> str:
        """Format a single cell according to the UI rules."""

        if value is None:
            return ""

        field_meta = self._ctx.meta.fields_by_name.get(column_name)
        dp = max(0, min(30, int(self._ctx.measure_decimal_places)))
        if field_meta is not None and field_formats_as_measure(field_meta):
            try:
                num = float(value)  # type: ignore[arg-type]
                if num != num:
                    return "—"
                return f"{num:,.{dp}f}"
            except Exception:
                return str(value)

        # Derived numeric columns like agg_value/cnt also get numeric formatting.
        if column_name in {"agg_value", "y_value", "cnt"}:
            try:
                num = float(value)  # type: ignore[arg-type]
                if num != num:
                    return "—"
                return f"{num:,.{dp}f}" if column_name != "cnt" else f"{int(num):,}"
            except Exception:
                return str(value)

        if field_meta is not None and field_displays_as_yyyymmdd(field_meta):
            return format_yyyymmdd_display(value)

        return str(value)

    def _is_measure_column(self, column_name: str) -> bool:
        """Return whether a chart table column should be treated as numeric."""

        field_meta = self._ctx.meta.fields_by_name.get(column_name)
        if field_meta is not None:
            return field_formats_as_measure(field_meta)
        return column_name in {"agg_value", "y_value", "cnt"}

    def _sync_chart_filter_geometry(self) -> None:
        """Align chart-table filter inputs with header section widths and scroll position."""

        if (
            self._chart_filter_corner is None
            or self._chart_filter_inner is None
            or self._chart_filter_scroll is None
        ):
            return
        if not self._chart_filter_line_edits:
            cw = max(self._chart_table.verticalHeader().width(), 1)
            self._chart_filter_corner.setFixedSize(cw, self._CHART_FILTER_STRIP_ROW)
            self._chart_filter_inner.setFixedWidth(cw + 1)
            return

        vh = self._chart_table.verticalHeader()
        hdr = self._chart_table.horizontalHeader()
        cw = max(vh.width(), 1)
        self._chart_filter_corner.setFixedSize(cw, self._CHART_FILTER_STRIP_ROW)

        for i, le in enumerate(self._chart_filter_line_edits):
            if i >= hdr.count():
                break
            w = max(hdr.sectionSize(i), 24)
            le.setFixedWidth(w)

        inner_w = cw + sum(
            max(hdr.sectionSize(i), 24) for i in range(len(self._chart_filter_line_edits))
        )
        self._chart_filter_inner.setFixedWidth(max(inner_w, cw + 1))

        tb_bar = self._chart_table.horizontalScrollBar()
        fs_bar = self._chart_filter_scroll.horizontalScrollBar()
        fs_bar.setMinimum(tb_bar.minimum())
        fs_bar.setMaximum(tb_bar.maximum())
        fs_bar.setSingleStep(tb_bar.singleStep())
        fs_bar.setPageStep(tb_bar.pageStep())
        fs_bar.setValue(tb_bar.value())

    def _wire_chart_table_filter_signals(self) -> None:
        """Connect header resize/scroll once so the filter strip tracks the table."""

        if self._chart_filter_wired:
            return
        hdr = self._chart_table.horizontalHeader()
        hdr.sectionResized.connect(lambda *_a: self._sync_chart_filter_geometry())  # type: ignore[attr-defined]
        hdr.geometriesChanged.connect(lambda *_a: self._sync_chart_filter_geometry())  # type: ignore[attr-defined]
        vh = self._chart_table.verticalHeader()
        vh.geometriesChanged.connect(lambda *_a: self._sync_chart_filter_geometry())  # type: ignore[attr-defined]
        self._chart_table.horizontalScrollBar().valueChanged.connect(  # type: ignore[attr-defined]
            self._chart_filter_scroll.horizontalScrollBar().setValue
        )
        self._chart_filter_scroll.horizontalScrollBar().valueChanged.connect(  # type: ignore[attr-defined]
            self._chart_table.horizontalScrollBar().setValue
        )
        self._chart_filter_wired = True

    def _setup_chart_column_filters(self) -> None:
        """Build the per-column filter strip above the chart table (Data Grid style)."""

        if self._chart_filter_layout is None:
            return
        while self._chart_filter_layout.count() > 1:
            item = self._chart_filter_layout.takeAt(1)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._chart_filter_line_edits = []
        self._chart_search_inputs = {}
        rh = self._CHART_FILTER_STRIP_ROW - 2
        for col in self._chart_columns:
            le = QLineEdit()
            if self._is_measure_column(col):
                le.setPlaceholderText("Text or min-max")
            else:
                le.setPlaceholderText("Filter")
            le.setStyleSheet(
                "QLineEdit { padding: 2px 4px; font-size: 9px; background: #FFFFFF; "
                "border: 1px solid #BBD7F0; border-radius: 3px; }"
            )
            le.setFixedHeight(rh)
            if hasattr(le, "setClearButtonEnabled"):
                try:
                    le.setClearButtonEnabled(True)  # type: ignore[attr-defined]
                except Exception:
                    pass
            le.textChanged.connect(self._apply_column_filters)  # type: ignore[attr-defined]
            self._chart_filter_layout.addWidget(le)
            self._chart_filter_line_edits.append(le)
            self._chart_search_inputs[col] = le

    def _apply_column_filters(self) -> None:
        """Apply per-column quick filters and refresh the paginated chart table.

        Purpose
        -------
        Narrows displayed chart-table rows. **Measure** columns accept the same
        inclusive numeric range syntax as the Data Grid (``100-400``, ``100 and 400``,
        ``1..9``, ``1 to 9``); otherwise a case-insensitive substring match is used.
        **Dimension** columns always use substring match. Filtering preserves the
        current header sort order (rows are taken from the PK-sorted base).

        Internal Logic
        ---------------
        Recompute :attr:`_chart_all_display_rows` from :attr:`_chart_filter_source_rows`
        then repaints the current page via :meth:`_populate_current_chart_table_page`.
        """

        self._recompute_chart_all_display_rows()
        self._populate_current_chart_table_page()

    def _on_plotly_point_clicked(self, payload: str) -> None:
        """Handle plotly_click by filtering the chart table to the clicked dimension value.

        Purpose
        -------
        When the user clicks a bar/point/slice, keep only rows whose **chart
        dimension** column equals the clicked category (for example ``NAME`` =
        ``United States``), using the same dimension field order as the chart query.

        Internal Logic
        ---------------
        Parse JSON from the web bridge, then call
        :func:`filter_chart_rows_for_plotly_click` and
        :meth:`_rebuild_chart_table_from_rows`. The rebuild stores the filtered
        rows in :attr:`_chart_filter_source_rows` so quick-filters do not expand
        back to the full chart dataset.
        """

        try:
            import json

            customdata = json.loads(payload)
        except Exception:
            customdata = None

        chart_type = str(self._last_click_ctx.get("chart_type", ""))

        ctx_dims = [
            str(d)
            for d in (self._last_click_ctx.get("dims") or [])
            if d is not None and str(d).strip() != ""
        ]

        filtered = filter_chart_rows_for_plotly_click(
            chart_type,
            list(self._chart_columns),
            ctx_dims,
            list(self._chart_original_rows),
            customdata,
        )

        self._rebuild_chart_table_from_rows(filtered)

        # Push WHERE clause into Data Grid / Pivot Data tab sinks.
        where_sql = ""
        human_summary = ""
        try:
            where_sql = self._build_where_sql_from_click(chart_type, customdata)
            human_summary = self._build_chart_click_filter_human_summary(chart_type, customdata)
        except Exception:
            pass
        if self._grid_sink is not None:
            try:
                self._grid_sink.set_chart_click_filter(where_sql, human_summary=human_summary)
            except Exception:
                pass
        if self._pivot_sink is not None:
            try:
                self._pivot_sink.set_chart_click_filter(where_sql, human_summary=human_summary)
            except Exception:
                pass

    def _build_where_sql_from_click(self, chart_type: str, customdata: object) -> str:
        """Convert chart click info into a DuckDB WHERE clause."""

        def sql_quote(val: object) -> str:
            """Quote a Python value into a SQL literal."""

            if val is None:
                return "NULL"
            try:
                num = float(val)  # type: ignore[arg-type]
                if num != num:
                    return "NULL"
                return str(num)
            except Exception:
                s = str(val).replace("'", "''")
                return f"'{s}'"

        dims_eff = [
            str(d)
            for d in (self._last_click_ctx.get("dims") or [])
            if d is not None and str(d).strip() != ""
        ]
        if not dims_eff:
            dims_eff = list(self._selected_dimensions())
        measures = self._selected_measures()

        if chart_type in ("Bar", "Line", "Pie", "Scatter"):
            if not dims_eff or not customdata:
                return ""
            dim_col = dims_eff[0]
            tr = _plotly_click_dimension_token(customdata)
            token = _unwrap_plotly_click_value(tr) if tr is not None else None
            return f"{_sql_ident(dim_col)} = {sql_quote(token)}"

        if chart_type == "Stacked Bar":
            if len(dims_eff) < 2 or not (isinstance(customdata, list) and len(customdata) >= 2):
                return ""
            return (
                f"{_sql_ident(dims_eff[0])} = {sql_quote(_unwrap_plotly_click_value(customdata[0]))} AND "
                f"{_sql_ident(dims_eff[1])} = {sql_quote(_unwrap_plotly_click_value(customdata[1]))}"
            )

        if chart_type == "Histogram":
            if not measures or not (isinstance(customdata, list) and len(customdata) >= 3):
                return ""
            mcol = measures[0]
            left = float(customdata[1])
            right = float(customdata[2])
            return f"{_sql_ident(mcol)} >= {left} AND {_sql_ident(mcol)} <= {right}"

        return ""

    def _build_chart_click_filter_human_summary(self, chart_type: str, customdata: object) -> str:
        """Resolve chart context fields and delegate to :func:`build_chart_click_filter_human_summary`."""

        dims_eff = [
            str(d)
            for d in (self._last_click_ctx.get("dims") or [])
            if d is not None and str(d).strip() != ""
        ]
        if not dims_eff:
            dims_eff = list(self._selected_dimensions())
        return build_chart_click_filter_human_summary(
            chart_type,
            dims=dims_eff,
            measures=self._selected_measures(),
            customdata=customdata,
        )


class _PlotlyBridge(QObject):
    """Plotly↔Qt bridge object for plot click events."""

    def __init__(self, owner: VisualizeTab) -> None:
        super().__init__()
        self._owner = owner

    @pyqtSlot(str)
    def onPlotlyClick(self, payload: str) -> None:
        """Receive click payload from JS (camelCase slot name)."""

        self._owner._on_plotly_point_clicked(payload)

    @pyqtSlot(str)
    def on_plotly_click(self, payload: str) -> None:
        """Receive click payload from JS (snake_case slot name)."""

        self._owner._on_plotly_point_clicked(payload)


__all__ = ["VisualizeTab"]

