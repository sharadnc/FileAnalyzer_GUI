"""Data Grid tab UI for File Analyzer (filters, pagination, export).

This module implements the “Data Grid” experience described in the plan:

- Split filter area (Dimensions left, Measures right).
- Dimension filters: per-row field combo, a dropdown button titled ``{DimensionName} Filter``
  (or a selection count when checked), searchable checklist with Select All / Deselect All,
  and **Remove**.
- Measure filters: same pattern with ``{MeasureName} Filter`` on the range dropdown.
- Chart-click WHERE filter is supported (set by the Visualize tab).
- “Apply Filters” applies dimension/measure panel filters (and clears any chart-click filter).
- “Clear Filters” removes panel filters, chart-click filter, and column header quick filters.
- A hamburger drawer allows hiding/unhiding columns (with Select All /
  Deselect All and search inside the drawer).
- “Sort the Column Names” toggles between meta order and alphabetical order.
- Pagination uses 100 rows/page by default; the bar between **Prev** and **Next**
  shows **Filters - …** (applied panel filters, chart link, and live column header filters).
- The Pivot Data tab shows the same **Filters - …** line above the pivot shelf summary.
- Table rendering includes:
  - alternate-row highlighting
  - vertical gridlines
  - measure formatting (comma thousands + user-selected decimal places for ``M``; right-aligned)
  - dimension formatting (no thousands separators; left-aligned)
- Export:
  - “Export Filtered CSV” downloads a filtered CSV to the chosen path.
  - “Copy to Clipboard” copies the currently visible page as TSV with a
    header row so it pastes cleanly into Excel.
- Per-column quick filters: measure columns accept inclusive numeric ranges
    (for example ``100-400`` or ``100 and 400``); other columns use substring match.
- Column-header sort: click sorts (toggle asc/desc on the same sole column); Ctrl
    (or Cmd on macOS) + click adds/toggles columns for multi-key ``ORDER BY``;
    Shift+click resets to primary-key order only. Sort arrows show on headers;
    primary keys are always appended as a stable tie-break in SQL.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, cast

from file_analyzer.config import load_app_config
from file_analyzer.meta_parser import (
    FieldMeta,
    field_displays_as_yyyymmdd,
    field_formats_as_measure,
    field_in_dimension_panels,
    field_in_measure_panels,
    format_yyyymmdd_display,
)
from file_analyzer.pivot_hierarchy import build_pivot_leaf_sql, run_pivot_pipeline
from file_analyzer.ui.models import LoadedDatasetContext
from file_analyzer.ui.quick_stats_tooltips import quick_stats_tooltips_by_field_name

try:
    from PyQt5.QtCore import QEvent, QObject, QPoint, QRect, Qt, QThread, QTimer, pyqtSignal
    from PyQt5.QtGui import QClipboard, QColor, QFont, QMouseEvent
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError:  # pragma: no cover
    QEvent = object  # type: ignore[assignment]
    QObject = object  # type: ignore[assignment]
    QPoint = object  # type: ignore[assignment]
    QRect = object  # type: ignore[assignment]
    Qt = object  # type: ignore[assignment]
    QThread = object  # type: ignore[assignment]
    QTimer = object  # type: ignore[assignment]
    pyqtSignal = object  # type: ignore[assignment]
    QClipboard = object  # type: ignore[assignment]
    QColor = object  # type: ignore[assignment]
    QFont = object  # type: ignore[assignment]
    QMouseEvent = object  # type: ignore[assignment]
    QApplication = object  # type: ignore[assignment]
    QCheckBox = object  # type: ignore[assignment]
    QComboBox = object  # type: ignore[assignment]
    QFileDialog = object  # type: ignore[assignment]
    QFrame = object  # type: ignore[assignment]
    QHBoxLayout = object  # type: ignore[assignment]
    QLabel = object  # type: ignore[assignment]
    QLineEdit = object  # type: ignore[assignment]
    QListWidget = object  # type: ignore[assignment]
    QListWidgetItem = object  # type: ignore[assignment]
    QMessageBox = object  # type: ignore[assignment]
    QPushButton = object  # type: ignore[assignment]
    QScrollArea = object  # type: ignore[assignment]
    QSizePolicy = object  # type: ignore[assignment]
    QSplitter = object  # type: ignore[assignment]
    QTableWidget = object  # type: ignore[assignment]
    QTableWidgetItem = object  # type: ignore[assignment]
    QVBoxLayout = object  # type: ignore[assignment]
    QWidget = object  # type: ignore[assignment]
    QAbstractItemView = object  # type: ignore[assignment]

@dataclass(frozen=True)
class RangeSpec:
    """Numeric range specification used for measure filters."""

    start: float
    end: float
    label: str
    include_end: bool


def _widget_contains_global_point(widget: QWidget, global_pos: QPoint) -> bool:
    """Return True if ``global_pos`` lies inside ``widget``'s on-screen rectangle.

    Purpose
    -------
    Support popup dismissal logic by hit-testing the anchor button and popup
    frame against the global mouse position from a ``QMouseEvent``.

    Internal Logic
    ---------------
    Map the widget's local origin to global coordinates, build a ``QRect`` with
    the widget's width and height, then call ``QRect.contains`` with the global
    point.

    Example invocation
    --------------------
    ``_widget_contains_global_point(self._drop_btn, event.globalPos())`` → ``True``
    when the user pressed inside the dropdown button.

    Args:
        widget: Any visible ``QWidget`` (typically the anchor or popup).
        global_pos: Global pixel coordinates from ``QMouseEvent.globalPos()``.

    Returns:
        ``True`` if the point is inside the widget's bounds.
    """

    top_left: QPoint = widget.mapToGlobal(QPoint(0, 0))
    rect = QRect(int(top_left.x()), int(top_left.y()), int(widget.width()), int(widget.height()))
    return bool(rect.contains(global_pos))


class _AppMousePopupCloser(QObject):
    """Close a checklist popup when the user presses the mouse outside it.

    Purpose
    -------
    ``Qt.Popup`` frames do not always receive consistent outside-click dismissal
    across platforms; this ``QObject`` event filter mirrors combo-box behavior by
    hiding the popup when a mouse press occurs outside both the popup and its
    anchor button.

    Internal Logic
    ---------------
    - Registered on ``QApplication`` via ``installEventFilter``.
    - On ``QEvent.MouseButtonPress``, if the popup is visible and the press is
      outside the popup and outside the anchor, call ``popup.hide()`` and consume
      the event.

    Example invocation
    --------------------
    ``closer = _AppMousePopupCloser(popup_frame, anchor_button)`` then
    ``QApplication.instance().installEventFilter(closer)``.
    """

    def __init__(self, popup: QFrame, anchor: QPushButton) -> None:
        """Store references to the floating popup and its anchor control."""

        super().__init__(popup)
        self._popup: QFrame = popup
        self._anchor: QPushButton = anchor

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        """Hide the popup on an outside mouse press; otherwise ignore."""

        try:
            if event.type() != QEvent.MouseButtonPress:
                return False
            if not self._popup.isVisible():
                return False
            mouse_evt = cast(QMouseEvent, event)
            gp = mouse_evt.globalPos()
            if _widget_contains_global_point(self._popup, gp) or _widget_contains_global_point(self._anchor, gp):
                return False
            self._popup.hide()
            return True
        except Exception:
            return False


def _sql_quote(val: object) -> str:
    """Quote a value into a DuckDB SQL literal.

    Purpose
    -------
    Convert Python values (strings, numbers) into a SQL literal string that
    DuckDB can interpret safely for this controlled UI use case.

    Internal Logic
    ---------------
    - Try to interpret as float: numbers become unquoted SQL literals.
    - Otherwise treat as string and single-quote with embedded quote escaping.
    """

    if val is None:
        return "NULL"

    # Try numeric.
    try:
        num = float(val)  # type: ignore[arg-type]
        if math.isnan(num):
            return "NULL"
        return str(num)
    except Exception:
        pass

    s = str(val).replace("'", "''")
    return f"'{s}'"


def _format_measure_cell(value: object, decimal_places: int) -> str:
    """Format a measure value with thousands separators and fixed decimals.

    Purpose
    -------
    Display DuckDB measure cells consistently with the user's **# of decimals**
    choice from the main window submit strip.

    Internal Logic
    ---------------
    Clamp ``decimal_places`` to ``[0, 30]``, coerce to ``float``, and format with
    ``:,`` plus a dynamic ``.{n}f`` precision.

    Example invocation
    --------------------
    ``_format_measure_cell(1234.5, 2)`` → ``"1,234.50"``; ``None`` → ``""``.
    """

    dp = max(0, min(30, int(decimal_places)))
    if value is None:
        return ""
    try:
        num = float(value)  # type: ignore[arg-type]
        if num != num:
            return "—"
        return f"{num:,.{dp}f}"
    except Exception:
        return str(value)


def _format_dimension_cell(value: object, field_meta: Optional[FieldMeta] = None) -> str:
    """Format a dimension value without thousands separators.

    When ``field_meta`` is a ``YYYYMMDD`` FieldType column, use
    :func:`~file_analyzer.meta_parser.format_yyyymmdd_display`.
    """

    if field_meta is not None and field_displays_as_yyyymmdd(field_meta):
        return format_yyyymmdd_display(value)

    if value is None:
        return ""
    try:
        num = float(value)  # type: ignore[arg-type]
        if num != num:
            return "—"
        # Keep fixed decimals for numeric-like dimensions without comma separators.
        return f"{num:.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value)


def _grid_sql_ident(name: str) -> str:
    """Return a DuckDB double-quoted identifier for a column name in live-filter SQL.

    Purpose
    -------
    Live filters interpolate column names into ``WHERE`` fragments; quoting avoids
    reserved-word collisions (for example ``NAME``) and supports embedded quotes.

    Internal Logic
    ---------------
    Wrap ``name`` in double quotes and double any embedded ``"`` characters.

    Example invocation
    --------------------
    ``_grid_sql_ident("POPESTIMATE2024")`` produces ``"POPESTIMATE2024"``.
    """

    return '"' + str(name).replace('"', '""') + '"'


def _build_grid_order_by_sql(
    user_sort_keys: Sequence[Tuple[str, bool]],
    file_key_columns: Sequence[str],
) -> str:
    """Build a DuckDB ``ORDER BY`` column list (no ``ORDER BY`` keyword).

    Purpose
    -------
    Compose deterministic ordering for the grid and CSV export: user-selected
    keys first (each ``ASC``/``DESC``), then every primary-key column not already
    listed, always ``ASC``, so pages stay stable when values tie.

    Internal Logic
    ---------------
    - Emit ``"col" ASC|DESC`` for each ``(col, ascending)`` in order, skipping
      duplicate column names.
    - Append file-key columns in meta order if missing, as ``"pk" ASC``.
    - If nothing remains (no keys and no PK metadata), return ``"1"`` so SQL is
      still valid.

    Example invocation
    --------------------
    ``_build_grid_order_by_sql([("STATE", False)], ["SUMLEV", "STATE"])`` →
    ``'"STATE" DESC, "SUMLEV" ASC'`` — ``STATE`` is already in ``seen``, so the
    trailing ``"STATE" ASC`` from file keys is skipped.
    """

    terms: list[str] = []
    seen: set[str] = set()
    for col, asc in user_sort_keys:
        if col in seen:
            continue
        direction = "ASC" if asc else "DESC"
        terms.append(f"{_grid_sql_ident(col)} {direction}")
        seen.add(col)
    for pk in file_key_columns:
        if pk not in seen:
            terms.append(f"{_grid_sql_ident(pk)} ASC")
            seen.add(pk)
    return ", ".join(terms) if terms else "1"


def _parse_measure_range_bounds(raw: str) -> Optional[Tuple[float, float]]:
    """Parse an inclusive ``low..high`` numeric range from a measure quick-filter string.

    Purpose
    -------
    Measure columns accept compact range syntax (hyphen, ``and``, ``to``, or
    ``..``) so users can filter ``BETWEEN`` two bounds without substring matching.

    Internal Logic
    ---------------
    - Strip and normalize dash variants to ASCII ``-``.
    - Try regexes in order: ``a - b``, ``a..b``, ``a and b``, ``a to b`` (word
      boundaries, case-insensitive for words).
    - Parse both tokens as floats; reject NaN/Inf; swap so ``low <= high``.

    Example invocation
    --------------------
    ``_parse_measure_range_bounds("  100 and 400 ")`` → ``(100.0, 400.0)``;
    ``_parse_measure_range_bounds("400-100")`` → ``(100.0, 400.0)``;
    ``_parse_measure_range_bounds("abc")`` → ``None``.
    """

    s = raw.strip()
    if not s:
        return None
    s = s.replace("–", "-").replace("—", "-")

    patterns: tuple[re.Pattern[str], ...] = (
        re.compile(
            r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*-\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)$"
        ),
        re.compile(
            r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*\.\.\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)$"
        ),
        re.compile(
            r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s+and\s+([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s+to\s+([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)$",
            re.IGNORECASE,
        ),
    )
    for pat in patterns:
        m = pat.match(s)
        if not m:
            continue
        try:
            a = float(m.group(1))
            b = float(m.group(2))
        except ValueError:
            continue
        if math.isnan(a) or math.isnan(b) or math.isinf(a) or math.isinf(b):
            continue
        return (a, b) if a <= b else (b, a)
    return None


def _sql_double_literal(x: float) -> str:
    """Format a finite float as a DuckDB-compatible numeric literal (no quotes)."""

    if math.isnan(x) or math.isinf(x):
        raise ValueError("non-finite float")
    return format(x, ".16g")


class _GridQueryWorker(QObject):
    """Worker that queries a single page of the grid from DuckDB."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        database_path: Path,
        table_name: str,
        where_sql: str,
        order_by_sql: str,
        visible_columns: Sequence[str],
        limit: int,
        offset: int,
        host_thread: QThread,
    ) -> None:
        super().__init__()
        self._database_path = database_path
        self._table_name = table_name
        self._where_sql = where_sql
        self._order_by_sql = order_by_sql
        self._visible_columns = list(visible_columns)
        self._limit = limit
        self._offset = offset
        self._host_thread = host_thread

    def run(self) -> None:
        """Execute the page query and return (rows, columns, total_rows)."""

        try:
            import duckdb  # type: ignore

            where_clause = f"WHERE {self._where_sql}" if self._where_sql.strip() else ""
            conn = duckdb.connect(database=str(self._database_path))
            try:
                total_rows = conn.execute(
                    f"SELECT COUNT(*) FROM {self._table_name} {where_clause}"
                ).fetchone()[0]

                visible_cols_sql = ", ".join(self._visible_columns)
                # Stable page slices: ORDER BY file keys (same as meta PK order).
                sql = f"""
                    SELECT {visible_cols_sql}
                    FROM {self._table_name}
                    {where_clause}
                    ORDER BY {self._order_by_sql}
                    LIMIT {int(self._limit)}
                    OFFSET {int(self._offset)};
                """
                page_rows = conn.execute(sql).fetchall()
                columns = list(self._visible_columns)
                self.finished.emit({"rows": page_rows, "columns": columns, "total": int(total_rows)})
            finally:
                conn.close()
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self._host_thread.quit()


class _PivotLeafWorker(QObject):
    """Worker: DuckDB leaf aggregates then Python Excel-style pivot layout."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        database_path: Path,
        table_name: str,
        base_where_sql: str,
        row_dims: Sequence[str],
        col_dims: Sequence[str],
        measures: Sequence[str],
        agg: str,
        host_thread: QThread,
    ) -> None:
        """Store DuckDB parameters and host thread for :meth:`run`."""

        super().__init__()
        self._database_path = database_path
        self._table_name = table_name
        self._base_where_sql = base_where_sql
        self._row_dims = list(row_dims)
        self._col_dims = list(col_dims)
        self._measures = list(measures)
        self._agg = agg
        self._host_thread = host_thread

    def run(self) -> None:
        """Fetch grouped leaves, build wide pivot + subtotal rows, emit render payload."""

        try:
            import duckdb  # type: ignore

            sql = build_pivot_leaf_sql(
                table_name=self._table_name,
                base_where_sql=self._base_where_sql,
                row_dims=self._row_dims,
                col_dims=self._col_dims,
                measures=self._measures,
                agg=self._agg,
            )
            conn = duckdb.connect(database=str(self._database_path))
            try:
                leaf_rows = conn.execute(sql).fetchall()
            finally:
                conn.close()

            cols, disp_rows, kinds, row_depths, expandable, _echo = run_pivot_pipeline(
                leaf_rows=leaf_rows,
                row_dims=self._row_dims,
                col_dims=self._col_dims,
                measures=self._measures,
                agg=self._agg,
                sql_executed=sql,
            )
            tuple_rows: list[Tuple[object, ...]] = [tuple(r) for r in disp_rows]
            self.finished.emit(
                {
                    "rows": tuple_rows,
                    "columns": cols,
                    "pivot_row_dims": list(self._row_dims),
                    "row_kinds": kinds,
                    "row_depths": row_depths,
                    "pivot_expandable": expandable,
                    "total": len(tuple_rows),
                    "sql": sql,
                }
            )
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self._host_thread.quit()


class DataGridTab(QWidget):
    """Main Data Grid tab with split filters, pagination, and export.

    When constructed with ``ui_surface="pivot"``, the lower area is a pivot
    field selector plus a single summary table instead of the paginated raw grid
    and per-column filter strip (see :class:`~file_analyzer.ui.pivot_tab.PivotDataTab`).
    """

    # Pixel height for the per-column filter row (must fit inside ``QScrollArea`` without
    # sharing vertical space with a horizontal scrollbar — filter bar uses ``AlwaysOff``).
    _FILTER_STRIP_ROW_HEIGHT: int = 28

    def __init__(self, ctx: LoadedDatasetContext, *, ui_surface: str = "grid") -> None:
        super().__init__()
        if ui_surface not in ("grid", "pivot"):
            raise ValueError(f"ui_surface must be 'grid' or 'pivot', not {ui_surface!r}")
        self._ui_surface = ui_surface
        self._ctx = ctx
        self._config = load_app_config()

        # Chart-click bridge.
        self._chart_click_filter_where_sql: str = ""
        self._chart_click_filter_human_summary: str = ""
        self._active_mode: str = "none"  # none | chart | user

        # User-applied filters WHERE clause (only when Apply Filters is clicked).
        self._user_filters_where_sql: str = ""
        # Human-readable dimension/measure summary captured on Apply Filters.
        self._applied_panel_filter_summary: str = ""

        # Column visibility state: on load, show every column from the loaded file.
        self._visible_columns_set: set[str] = {f.name for f in ctx.meta.fields}
        self._sort_columns_by_name: bool = False

        # User header sort: ordered (column_name, ascending); empty => ORDER BY PKs only.
        self._grid_sort_specs: list[Tuple[str, bool]] = []

        # Pagination state.
        self._page_size: int = int(self._config.page_size_default)
        self._page_index: int = 0
        self._total_rows: int = 0

        # Pivot tree: row depths / expand flags from last refresh; collapsed subtotal indices.
        self._pivot_row_depths: list[int] = []
        self._pivot_expandable_flags: list[bool] = []
        self._pivot_collapsed_rows: set[int] = set()
        self._pivot_tree_click_wired: bool = False
        self._last_pivot_sql: str = ""
        self._last_pivot_row_dims: list[str] = []
        self._last_pivot_row_kinds: list[str] = []
        self._pivot_selection_summary_label: Optional[QLabel] = None
        self._pivot_filters_summary_label: Optional[QLabel] = None
        self._pivot_fields_splitter: Optional[QSplitter] = None
        self._pivot_results_splitter: Optional[QSplitter] = None

        # Store UI filter rows.
        self._dimension_filter_rows: list[dict[str, object]] = []
        self._measure_filter_rows: list[dict[str, object]] = []

        self._column_drawer_visible: bool = False

        # Keep QThread/QObject references as instance attributes to prevent
        # PyQt from garbage-collecting them while they are running.
        self._grid_thread: Optional[QThread] = None
        self._grid_worker: Optional[_GridQueryWorker] = None
        self._export_thread: Optional[QThread] = None
        self._export_worker: Optional[QObject] = None

        self._drawer_updating: bool = False

        self._last_grid_columns: Optional[list[str]] = None
        self._column_filter_inputs: list[QLineEdit] = []
        self._column_filter_header_wired: bool = False
        # One-shot texts keyed by column name, applied on next :meth:`_rebuild_column_filter_row`.
        self._session_header_filter_texts: Dict[str, str] = {}

        self._live_column_filter_timer = QTimer(self)
        self._live_column_filter_timer.setSingleShot(True)
        self._live_column_filter_timer.setInterval(280)
        self._live_column_filter_timer.timeout.connect(self._apply_live_column_filter_query)  # type: ignore[attr-defined]

        self._build_ui()
        self._refresh_surface_async()

    def showEvent(self, event: object) -> None:
        """Resync filter strip layout when the tab is shown (header widths may update late).

        Purpose
        -------
        After switching to the Data Grid tab or first show, ``QHeaderView`` geometry
        can settle on the next event-loop tick; a deferred ``_sync_column_filter_geometry``
        keeps filter boxes aligned with column sections.

        Internal Logic
        ---------------
        Call the base ``showEvent``, then queue :meth:`_sync_column_filter_geometry` via
        ``QTimer.singleShot(0, ...)`` so it runs after layout.

        Example invocation
        --------------------
        Qt invokes this automatically when the user selects the Data Grid tab.
        """

        try:
            super().showEvent(event)  # type: ignore[misc]
        except Exception:
            pass
        if self._ui_surface != "grid":
            return
        try:
            QTimer.singleShot(0, self._sync_column_filter_geometry)  # type: ignore[attr-defined]
        except Exception:
            pass

    def wait_for_background_threads(self) -> None:
        """Block until grid/export background QThreads finish (window close).

        Purpose
        -------
        Avoid ``QThread: Destroyed while thread is still running`` when the main
        window closes while DuckDB queries are in flight on worker threads.

        Internal Logic
        ----------------
        Wait on ``_grid_thread`` and ``_export_thread`` when their ``isRunning``
        flag is true.

        Example invocation
        --------------------
        Called from the main window ``closeEvent`` before the tab widgets are destroyed.
        """

        for t in (self._grid_thread, self._export_thread):
            if t is not None and t.isRunning():
                t.wait()

    def _build_ui(self) -> None:
        """Create the Data Grid UI widgets and connect signals."""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        # Dimension / measure filter columns (horizontally resizable).
        left_filters_widget = QWidget()
        left_filters = QVBoxLayout(left_filters_widget)
        left_filters.setContentsMargins(0, 0, 0, 0)
        left_filters.addWidget(QLabel("Dimensions Filters"))

        self._dimension_rows_container = QVBoxLayout()
        self._dimension_rows_container.setSpacing(4)
        left_filters.addLayout(self._dimension_rows_container, 0)
        left_filters.addStretch(1)

        self._add_more_dim_btn = QPushButton("Add More Dimension Filters")
        left_filters.addWidget(self._add_more_dim_btn, 0)

        right_filters_widget = QWidget()
        right_filters = QVBoxLayout(right_filters_widget)
        right_filters.setContentsMargins(0, 0, 0, 0)
        right_filters.addWidget(QLabel("Measures Filters"))

        self._measure_rows_container = QVBoxLayout()
        self._measure_rows_container.setSpacing(4)
        right_filters.addLayout(self._measure_rows_container, 0)
        right_filters.addStretch(1)

        self._add_more_meas_btn = QPushButton("Add More Measure Filters")
        right_filters.addWidget(self._add_more_meas_btn, 0)

        self._filter_splitter = QSplitter(Qt.Horizontal)
        self._filter_splitter.setChildrenCollapsible(False)
        self._filter_splitter.addWidget(left_filters_widget)
        self._filter_splitter.addWidget(right_filters_widget)
        self._filter_splitter.setStretchFactor(0, 1)
        self._filter_splitter.setStretchFactor(1, 1)

        # Default: 1 filter row on each side.
        self._add_dimension_filter_row()
        self._add_measure_filter_row()

        self._add_more_dim_btn.clicked.connect(self._add_dimension_filter_row)  # type: ignore[attr-defined]
        self._add_more_meas_btn.clicked.connect(self._add_measure_filter_row)  # type: ignore[attr-defined]

        toolbar_row = QHBoxLayout()

        self._btn_menu = QPushButton("Columns")
        toolbar_row.addWidget(self._btn_menu, 0)

        self._sort_columns_checkbox = QCheckBox("Sort the Column Names")
        self._sort_columns_checkbox.setChecked(False)
        toolbar_row.addWidget(self._sort_columns_checkbox, 1)

        self._apply_filters_btn = QPushButton("Apply Filters")
        toolbar_row.addWidget(self._apply_filters_btn, 0)

        self._clear_filters_btn = QPushButton("Clear Filters")
        self._clear_filters_btn.setToolTip(
            "Remove dimension/measure panel filters, chart-click filter, and column header quick filters."
        )
        toolbar_row.addWidget(self._clear_filters_btn, 0)

        self._copy_excel_btn = QPushButton("Copy to Clipboard")
        self._copy_excel_btn.setToolTip(
            "Copy the current page to the clipboard as TSV with a header row (pastes cleanly into Excel)."
        )
        toolbar_row.addWidget(self._copy_excel_btn, 0)

        self._export_csv_btn = QPushButton("Export Filtered CSV")
        toolbar_row.addWidget(self._export_csv_btn, 0)

        # Hamburger drawer (simple toggle panel for this implementation).
        self._drawer_container = QWidget()
        self._drawer_layout = QVBoxLayout(self._drawer_container)
        self._drawer_container.setVisible(False)

        # Drawer: search + select all/deselect all + list.
        self._drawer_search = QLineEdit()
        self._drawer_search.setPlaceholderText("Search columns in drawer")
        self._drawer_layout.addWidget(self._drawer_search)

        drawer_buttons = QHBoxLayout()
        self._drawer_select_all = QCheckBox("Select All")
        self._drawer_deselect_all = QCheckBox("Deselect All")
        drawer_buttons.addWidget(self._drawer_select_all)
        drawer_buttons.addWidget(self._drawer_deselect_all)
        self._drawer_layout.addLayout(drawer_buttons)

        self._drawer_list = QListWidget()
        self._drawer_list.setSelectionMode(QListWidget.MultiSelection)
        self._drawer_layout.addWidget(self._drawer_list, 1)

        # Keep visible column set in sync with individual drawer checkbox toggles.
        try:
            self._drawer_list.itemChanged.connect(self._on_drawer_item_changed)  # type: ignore[attr-defined]
        except Exception:
            pass

        # Pagination bar (center: filters applied; bottom: page counts).
        pager_row = QHBoxLayout()
        pager_row.setSpacing(8)
        self._prev_btn = QPushButton("Prev")
        self._next_btn = QPushButton("Next")

        self._pager_summary_label = QLabel("")
        self._pager_summary_label.setWordWrap(True)
        try:
            self._pager_summary_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._pager_summary_label.setMinimumHeight(22)

        self._page_label = QLabel("")
        try:
            self._page_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)  # type: ignore[attr-defined]
        except Exception:
            pass

        pager_center = QWidget()
        pager_center_layout = QVBoxLayout(pager_center)
        pager_center_layout.setContentsMargins(4, 2, 8, 2)
        pager_center_layout.setSpacing(2)
        pager_center_layout.addWidget(self._pager_summary_label, 1)
        pager_center_layout.addWidget(self._page_label, 0)

        pager_row.addWidget(self._prev_btn, 0)
        pager_row.addWidget(pager_center, 1)
        pager_row.addWidget(self._next_btn, 0)

        # Upper chrome (filters, toolbar, drawer, pager) vs grid: vertically resizable.
        upper_block = QWidget()
        upper_layout = QVBoxLayout(upper_block)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.addWidget(self._filter_splitter, 1)
        upper_layout.addLayout(toolbar_row, 0)
        upper_layout.addWidget(self._drawer_container, 0)
        upper_layout.addLayout(pager_row, 0)

        self._grid_table = QTableWidget()
        self._grid_table.setShowGrid(True)
        self._grid_table.setAlternatingRowColors(True)
        try:
            self._grid_table.horizontalHeader().setDefaultAlignment(int(Qt.AlignLeft | Qt.AlignVCenter))  # type: ignore[attr-defined]
        except Exception:
            pass

        if self._ui_surface == "pivot":
            self._btn_menu.setVisible(False)
            self._sort_columns_checkbox.setVisible(False)
            self._copy_excel_btn.setToolTip(
                "Copy the pivot summary to the clipboard as TSV (pastes cleanly into Excel)."
            )
            self._export_csv_btn.setText("Export Pivot CSV")
            self._prev_btn.setVisible(False)
            self._next_btn.setVisible(False)

            pivot_chrome = self._build_pivot_field_bar()
            self._pivot_filters_summary_label = QLabel("")
            self._pivot_filters_summary_label.setWordWrap(True)
            try:
                self._pivot_filters_summary_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._pivot_filters_summary_label.setMinimumHeight(22)
            pivot_chrome.layout().addWidget(self._pivot_filters_summary_label, 0)  # type: ignore[union-attr]
            self._pivot_selection_summary_label = QLabel("")
            self._pivot_selection_summary_label.setWordWrap(True)
            try:
                self._pivot_selection_summary_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._pivot_selection_summary_label.setMinimumHeight(22)
            pivot_chrome.layout().addWidget(self._pivot_selection_summary_label, 0)  # type: ignore[union-attr]

            self._pivot_results_splitter = QSplitter(Qt.Vertical)  # type: ignore[attr-defined]
            self._pivot_results_splitter.setChildrenCollapsible(False)
            self._pivot_results_splitter.addWidget(pivot_chrome)
            self._pivot_results_splitter.addWidget(self._grid_table)
            self._pivot_results_splitter.setStretchFactor(0, 0)
            self._pivot_results_splitter.setStretchFactor(1, 1)
            try:
                self._pivot_results_splitter.setSizes([320, 480])
            except Exception:
                pass

            lower_panel = self._pivot_results_splitter
            try:
                self._pivot_rows_list.itemSelectionChanged.connect(self._update_pivot_selection_summary_label)  # type: ignore[attr-defined]
                self._pivot_cols_list.itemSelectionChanged.connect(self._update_pivot_selection_summary_label)  # type: ignore[attr-defined]
                self._pivot_vals_list.itemSelectionChanged.connect(self._update_pivot_selection_summary_label)  # type: ignore[attr-defined]
                self._pivot_agg_combo.currentIndexChanged.connect(self._update_pivot_selection_summary_label)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._update_pivot_selection_summary_label()
            self._update_pager_context_label()
            if not self._pivot_tree_click_wired:
                self._grid_table.cellClicked.connect(self._on_pivot_tree_cell_clicked)  # type: ignore[attr-defined]
                self._pivot_tree_click_wired = True
        else:
            # Scrollable row of per-column filter boxes aligned with header sections.
            self._filter_scroll = QScrollArea()
            self._filter_scroll.setFrameShape(QFrame.NoFrame)  # type: ignore[attr-defined]
            self._filter_scroll.setWidgetResizable(False)
            try:
                # Hide the filter strip's horizontal scrollbar so its height is not consumed
                # by the bar (which made the filter row effectively invisible). Scrolling
                # still follows the table via ``setValue`` on this scrollbar.
                self._filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # type: ignore[attr-defined]
                self._filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # type: ignore[attr-defined]
                self._filter_scroll.setSizePolicy(
                    QSizePolicy.Expanding,  # type: ignore[attr-defined]
                    QSizePolicy.Fixed,  # type: ignore[attr-defined]
                )
            except Exception:
                pass
            self._filter_inner = QWidget()
            self._filter_inner.setFixedHeight(self._FILTER_STRIP_ROW_HEIGHT)
            self._filter_layout = QHBoxLayout(self._filter_inner)
            self._filter_layout.setContentsMargins(0, 0, 0, 0)
            self._filter_layout.setSpacing(0)
            self._filter_corner = QWidget()
            self._filter_corner.setFixedSize(1, self._FILTER_STRIP_ROW_HEIGHT)
            self._filter_layout.addWidget(self._filter_corner, 0)
            self._filter_scroll.setWidget(self._filter_inner)
            strip_h = self._FILTER_STRIP_ROW_HEIGHT + 2
            self._filter_scroll.setFixedHeight(strip_h)
            self._filter_scroll.setStyleSheet(
                "QScrollArea { background: #E8F2FB; border-bottom: 1px solid #86BDEB; }"
            )

            lower_panel = QWidget()
            grid_panel_layout = QVBoxLayout(lower_panel)
            grid_panel_layout.setContentsMargins(0, 0, 0, 0)
            grid_panel_layout.setSpacing(0)
            grid_panel_layout.addWidget(self._filter_scroll, 0)
            grid_panel_layout.addWidget(self._grid_table, 1)

        self._main_splitter = QSplitter(Qt.Vertical)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(upper_block)
        self._main_splitter.addWidget(lower_panel)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        outer.addWidget(self._main_splitter, 1)

        surf = "grid" if self._ui_surface == "grid" else "pivot"
        self._filter_splitter.setObjectName(f"fa_layout_{surf}_filter")
        self._main_splitter.setObjectName(f"fa_layout_{surf}_main_vertical")
        try:
            from PyQt5.QtCore import QSettings  # type: ignore[import-not-found]

            from file_analyzer.ui.layout_persistence import restore_splitter_state, wire_splitter_autosave

            _ls = QSettings()
            restore_splitter_state(_ls, self._filter_splitter, self._filter_splitter.objectName())
            restore_splitter_state(_ls, self._main_splitter, self._main_splitter.objectName())
            wire_splitter_autosave(self._filter_splitter, self._filter_splitter.objectName(), self)
            wire_splitter_autosave(self._main_splitter, self._main_splitter.objectName(), self)
            if self._ui_surface == "pivot" and self._pivot_fields_splitter is not None:
                self._pivot_fields_splitter.setObjectName("fa_layout_pivot_fields_horizontal")
                restore_splitter_state(_ls, self._pivot_fields_splitter, self._pivot_fields_splitter.objectName())
                wire_splitter_autosave(
                    self._pivot_fields_splitter,
                    self._pivot_fields_splitter.objectName(),
                    self,
                )
            if self._ui_surface == "pivot" and self._pivot_results_splitter is not None:
                self._pivot_results_splitter.setObjectName("fa_layout_pivot_results_vertical")
                restore_splitter_state(_ls, self._pivot_results_splitter, self._pivot_results_splitter.objectName())
                wire_splitter_autosave(
                    self._pivot_results_splitter,
                    self._pivot_results_splitter.objectName(),
                    self,
                )
        except Exception:
            pass

        # Style grid body, gridlines, and bold pastel-blue column headers (labels uppercased in code).
        self._grid_table.setStyleSheet(
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

        # Connect toolbar actions.
        self._btn_menu.clicked.connect(self._toggle_drawer)  # type: ignore[attr-defined]
        self._sort_columns_checkbox.stateChanged.connect(self._on_sort_toggle)  # type: ignore[attr-defined]
        self._apply_filters_btn.clicked.connect(self._on_apply_filters_clicked)  # type: ignore[attr-defined]
        self._clear_filters_btn.clicked.connect(self._on_clear_filters_clicked)  # type: ignore[attr-defined]
        self._prev_btn.clicked.connect(self._on_prev_page)  # type: ignore[attr-defined]
        self._next_btn.clicked.connect(self._on_next_page)  # type: ignore[attr-defined]
        self._copy_excel_btn.clicked.connect(self._on_copy_excel)  # type: ignore[attr-defined]
        self._export_csv_btn.clicked.connect(self._on_export_filtered_csv)  # type: ignore[attr-defined]

        if self._ui_surface == "grid":
            try:
                hdr = self._grid_table.horizontalHeader()
                hdr.sectionClicked.connect(self._on_grid_header_sort_clicked)  # type: ignore[attr-defined]
                hdr.setToolTip(
                    "Click: sort by column (toggle ↑/↓). Ctrl+click: add or toggle a multi-sort key. "
                    "Shift+click: reset to primary-key order. Arrows show the active sort."
                )
            except Exception:
                pass

        self._drawer_search.textChanged.connect(self._refresh_drawer_visibility)  # type: ignore[attr-defined]
        self._drawer_select_all.stateChanged.connect(self._on_drawer_select_all)  # type: ignore[attr-defined]
        self._drawer_deselect_all.stateChanged.connect(self._on_drawer_deselect_all)  # type: ignore[attr-defined]

        self._rebuild_drawer_list()

    def _build_pivot_field_column(self, title_text: str) -> Tuple[QWidget, QListWidget]:
        """Build one titled list column for the resizable pivot field splitter.

        Purpose
        -------
        Wrap a ``QListWidget`` with a caption for Rows, Columns, or Values.

        Internal Logic
        ----------------
        Use expanding size policy so the horizontal splitter can resize column width
        and height together with the vertical results splitter.

        Example invocation
        --------------------
        ``self._build_pivot_field_column(\"Rows (dimensions)\")``
        """

        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(4, 0, 4, 0)
        v.setSpacing(4)
        v.addWidget(QLabel(title_text))
        lw = QListWidget()
        lw.setSelectionMode(QAbstractItemView.ExtendedSelection)  # type: ignore[attr-defined]
        lw.setMinimumHeight(80)
        try:
            lw.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)  # type: ignore[attr-defined]
        except Exception:
            pass
        v.addWidget(lw, 1)
        wrap.setMinimumWidth(120)
        return wrap, lw

    def _add_pivot_field_items(self, list_widget: QListWidget, field_names: Sequence[str]) -> None:
        """Populate a pivot field list with quick-stats tooltips (same as Visualize tab).

        Purpose
        -------
        Rows, Columns, and Values lists should show NULL counts and field stats on hover.

        Internal Logic
        ----------------
        Build a name → HTML map from :func:`quick_stats_tooltips_by_field_name`, then add
        one ``QListWidgetItem`` per field and attach the tooltip when present.

        Example invocation
        --------------------
        ``self._add_pivot_field_items(self._pivot_rows_list, self._available_dimensions())``
        """

        tips = quick_stats_tooltips_by_field_name(self._ctx)
        for name in field_names:
            item = QListWidgetItem(name)
            tip = tips.get(name)
            if tip:
                item.setToolTip(tip)
            list_widget.addItem(item)

    def _build_pivot_field_bar(self) -> QWidget:
        """Lay out resizable Rows/Columns/Values lists and the control toolbar row.

        Purpose
        -------
        Mirror Excel’s pivot field list: dimensions for row labels, optional column
        dimensions, and measures—each in a horizontally resizable pane. Filter
        summary labels are attached by :meth:`_build_ui` below the control row.

        Internal Logic
        ---------------
        1. Title label row.
        2. Horizontal ``QSplitter`` with three field columns (stored on
           :attr:`_pivot_fields_splitter`).
        3. Control row: aggregate combo, **Update Pivot**, **Expand All**, **Collapse All**.

        Example invocation
        --------------------
        Called once from :meth:`_build_ui` when ``self._ui_surface == "pivot"``.

        Returns:
            Chrome widget (lists + controls); the results table is a sibling in
            :attr:`_pivot_results_splitter`.
        """

        bar = QWidget()
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        title = QLabel("Pivot layout — Rows (dimensions), Columns (optional dimensions), Values (measures)")
        try:
            title.setWordWrap(True)
        except Exception:
            pass
        outer.addWidget(title, 0)

        self._pivot_fields_splitter = QSplitter(Qt.Horizontal)  # type: ignore[attr-defined]
        self._pivot_fields_splitter.setChildrenCollapsible(False)

        rows_wrap, self._pivot_rows_list = self._build_pivot_field_column("Rows (dimensions)")
        cols_wrap, self._pivot_cols_list = self._build_pivot_field_column("Columns (optional - dimensions)")
        vals_wrap, self._pivot_vals_list = self._build_pivot_field_column("Values (measures)")
        self._pivot_fields_splitter.addWidget(rows_wrap)
        self._pivot_fields_splitter.addWidget(cols_wrap)
        self._pivot_fields_splitter.addWidget(vals_wrap)
        try:
            self._pivot_fields_splitter.setSizes([220, 220, 220])
        except Exception:
            pass
        outer.addWidget(self._pivot_fields_splitter, 1)

        self._add_pivot_field_items(self._pivot_rows_list, self._available_dimensions())
        self._add_pivot_field_items(self._pivot_cols_list, self._available_dimensions())
        self._add_pivot_field_items(self._pivot_vals_list, self._available_measures())

        if self._pivot_rows_list.count() > 0:
            self._pivot_rows_list.item(0).setSelected(True)
        if self._pivot_vals_list.count() > 0:
            self._pivot_vals_list.item(0).setSelected(True)

        controls_host = QWidget()
        controls_row = QHBoxLayout(controls_host)
        controls_row.setSpacing(8)
        agg_label = QLabel("Aggregate (applies to all values):")
        try:
            agg_label.setToolTip("Aggregation applied to every measure in Values.")
        except Exception:
            pass
        controls_row.addWidget(agg_label, 0)
        self._pivot_agg_combo = QComboBox()
        for a in ("SUM", "AVG", "MIN", "MAX", "COUNT"):
            self._pivot_agg_combo.addItem(a)
        self._pivot_agg_combo.setMaximumWidth(88)
        self._pivot_agg_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # type: ignore[attr-defined]
        controls_row.addWidget(self._pivot_agg_combo, 0)

        self._pivot_update_btn = QPushButton("Update Pivot")
        self._pivot_update_btn.setToolTip("Re-run DuckDB aggregation and rebuild the Excel-style layout.")
        self._pivot_update_btn.clicked.connect(self._refresh_pivot_async)  # type: ignore[attr-defined]
        controls_row.addWidget(self._pivot_update_btn, 0)

        controls_row.addStretch(1)

        self._pivot_expand_all_btn = QPushButton("Expand All")
        self._pivot_expand_all_btn.setToolTip("Show every row in the pivot hierarchy.")
        self._pivot_expand_all_btn.clicked.connect(self._on_pivot_expand_all_clicked)  # type: ignore[attr-defined]
        controls_row.addWidget(self._pivot_expand_all_btn, 0)

        self._pivot_collapse_all_btn = QPushButton("Collapse All")
        self._pivot_collapse_all_btn.setToolTip("Hide all nested rows under subtotals.")
        self._pivot_collapse_all_btn.clicked.connect(self._on_pivot_collapse_all_clicked)  # type: ignore[attr-defined]
        controls_row.addWidget(self._pivot_collapse_all_btn, 0)

        outer.addWidget(controls_host, 0)

        return bar

    def _pivot_selected_in_order(self, lst: QListWidget, universe: Sequence[str]) -> list[str]:
        """Return selected list widget texts in ``universe`` order (stable meta order)."""

        sel = {it.text() for it in lst.selectedItems()}
        return [x for x in universe if x in sel]

    def _refresh_surface_async(self) -> None:
        """Dispatch a data refresh for the active surface (raw grid vs pivot summary)."""

        if self._ui_surface == "pivot":
            self._refresh_pivot_async()
        else:
            self._refresh_grid_async()

    def _refresh_pivot_async(self) -> None:
        """Run DuckDB leaf aggregation then build the hierarchical pivot in a worker."""

        self._update_pivot_selection_summary_label()
        row_dims = self._pivot_selected_in_order(self._pivot_rows_list, self._available_dimensions())
        if not row_dims:
            self._update_pager_context_label(loading=False)
            QMessageBox.information(self, "Pivot", "Select at least one dimension under Rows.")
            return
        col_dims = self._pivot_selected_in_order(self._pivot_cols_list, self._available_dimensions())
        if set(col_dims) & set(row_dims):
            self._update_pager_context_label(loading=False)
            QMessageBox.warning(self, "Pivot", "Column dimensions cannot overlap row dimensions.")
            return
        measures = self._pivot_selected_in_order(self._pivot_vals_list, self._available_measures())
        if not measures:
            self._update_pager_context_label(loading=False)
            QMessageBox.information(self, "Pivot", "Select at least one measure under Values.")
            return

        agg = self._pivot_agg_combo.currentText()
        base_where = self._current_where_sql().strip()

        self._update_pager_context_label(loading=True)
        self._page_label.setText("Loading pivot…")

        if self._grid_thread is not None and self._grid_thread.isRunning():
            self._grid_thread.wait()
        self._grid_thread = None
        self._grid_worker = None

        thread = QThread()
        worker = _PivotLeafWorker(
            database_path=self._ctx.database_path,
            table_name=self._ctx.table_name,
            base_where_sql=base_where,
            row_dims=row_dims,
            col_dims=col_dims,
            measures=measures,
            agg=agg,
            host_thread=thread,
        )
        self._grid_thread = thread
        self._grid_worker = worker
        worker.moveToThread(thread)

        def on_finished(result: object) -> None:
            thread.wait()
            self._grid_thread = None
            self._grid_worker = None
            assert isinstance(result, dict)
            rows = result.get("rows", [])
            columns = list(result.get("columns", []))
            self._last_pivot_row_dims = list(result.get("pivot_row_dims", []))
            self._last_pivot_row_kinds = list(result.get("row_kinds", []))
            self._last_pivot_sql = str(result.get("sql", ""))
            n_rows = len(rows)
            rd = list(result.get("row_depths", []))
            ex = list(result.get("pivot_expandable", []))
            if len(rd) != n_rows:
                rd = (rd + [0] * n_rows)[:n_rows]
            if len(ex) != n_rows:
                ex = (ex + [False] * n_rows)[:n_rows]
            self._pivot_row_depths = rd
            self._pivot_expandable_flags = ex
            self._pivot_collapsed_rows.clear()
            self._total_rows = int(result.get("total", n_rows))
            self._render_grid(columns=columns, rows=list(rows))
            self._update_pivot_selection_summary_label()
            self._page_label.setText(f"{n_rows:,} pivot row(s)")
            self._update_pager_context_label(loading=False)

        def on_failed(msg: str) -> None:
            thread.wait()
            self._grid_thread = None
            self._grid_worker = None
            QMessageBox.critical(self, "Pivot refresh failed", msg)
            self._update_pager_context_label(loading=False)
            self._update_pivot_selection_summary_label()

        worker.finished.connect(on_finished)  # type: ignore[attr-defined]
        worker.failed.connect(on_failed)  # type: ignore[attr-defined]
        thread.started.connect(worker.run)  # type: ignore[attr-defined]
        thread.start()

    def _toggle_drawer(self) -> None:
        """Toggle the column drawer visibility."""

        self._drawer_container.setVisible(not self._drawer_container.isVisible())

    def _on_sort_toggle(self) -> None:
        """Handle sort column names checkbox toggling."""

        self._sort_columns_by_name = self._sort_columns_checkbox.isChecked()
        self._refresh_surface_async()

    def _available_dimensions(self) -> list[str]:
        """Return dimension field names for panels (excludes ``DISPLAY`` FieldType)."""

        return [f.name for f in self._ctx.meta.fields if field_in_dimension_panels(f)]

    def _available_measures(self) -> list[str]:
        """Return measure field names for panels (excludes ``DISPLAY`` and ``YYYYMMDD``)."""

        return [f.name for f in self._ctx.meta.fields if field_in_measure_panels(f)]

    def _sync_dimension_filter_combos(self) -> None:
        """Cascade dimension field combos: each field at most one row; exclude measures-in-use.

        Purpose
        -------
        When the user picks a dimension in one filter row, other dimension rows
        must not offer that same column. Optionally exclude names already chosen
        as measures so the two panels stay mutually exclusive when names overlap.

        Internal Logic
        ---------------
        Build ``blocked`` = other rows' selections plus current measure picks;
        rebuild each combo with ``allowed = all_dims - blocked`` (fallback to all
        if empty). Preserve the current value when still allowed; otherwise pick
        the first allowed entry. Finish by calling each row's ``reload_values``.

        Example invocation
        --------------------
        Called from ``_on_dimension_filter_field_changed`` after any dimension
        ``QComboBox`` emits ``currentIndexChanged``.
        """

        rows = self._dimension_filter_rows
        available = self._available_dimensions()
        if not rows or not available:
            return

        measure_reserved: set[str] = set()
        for row in self._measure_filter_rows:
            mc = row.get("field_combo")
            if isinstance(mc, QComboBox):
                measure_reserved.add(mc.currentText())

        for row in rows:
            cast(QComboBox, row["field_combo"]).blockSignals(True)
        try:
            for i, row in enumerate(rows):
                combo = cast(QComboBox, row["field_combo"])
                cur = combo.currentText()
                others: set[str] = set()
                for j, r2 in enumerate(rows):
                    if j == i:
                        continue
                    others.add(cast(QComboBox, r2["field_combo"]).currentText())
                blocked = others | measure_reserved
                allowed = [d for d in available if d not in blocked]
                if not allowed:
                    allowed = list(available)
                pick = cur if cur in allowed else allowed[0]
                combo.clear()
                for d in allowed:
                    combo.addItem(d)
                idx = combo.findText(pick)
                combo.setCurrentIndex(max(0, idx))
        finally:
            for row in rows:
                cast(QComboBox, row["field_combo"]).blockSignals(False)

        for row in rows:
            reload_fn = row.get("reload_values")
            if callable(reload_fn):
                reload_fn()

    def _sync_measure_filter_combos(self) -> None:
        """Cascade measure field combos: each field at most one row; exclude dimensions-in-use.

        Purpose
        -------
        Same cascading rules as :meth:`_sync_dimension_filter_combos`, applied to
        measure columns and excluding names already chosen on dimension filters.

        Internal Logic
        ---------------
        ``blocked`` combines other measure rows' selections with all current
        dimension filter picks, then each combo is repopulated and
        ``reload_ranges`` runs for every measure row.

        Example invocation
        --------------------
        Called from ``_on_measure_filter_field_changed`` and after adding a new
        measure filter row.
        """

        rows = self._measure_filter_rows
        available = self._available_measures()
        if not rows or not available:
            return

        dim_reserved: set[str] = set()
        for row in self._dimension_filter_rows:
            dc = row.get("field_combo")
            if isinstance(dc, QComboBox):
                dim_reserved.add(dc.currentText())

        for row in rows:
            cast(QComboBox, row["field_combo"]).blockSignals(True)
        try:
            for i, row in enumerate(rows):
                combo = cast(QComboBox, row["field_combo"])
                cur = combo.currentText()
                others: set[str] = set()
                for j, r2 in enumerate(rows):
                    if j == i:
                        continue
                    others.add(cast(QComboBox, r2["field_combo"]).currentText())
                blocked = others | dim_reserved
                allowed = [m for m in available if m not in blocked]
                if not allowed:
                    allowed = list(available)
                pick = cur if cur in allowed else allowed[0]
                combo.clear()
                for m in allowed:
                    combo.addItem(m)
                idx = combo.findText(pick)
                combo.setCurrentIndex(max(0, idx))
        finally:
            for row in rows:
                cast(QComboBox, row["field_combo"]).blockSignals(False)

        for row in rows:
            reload_fn = row.get("reload_ranges")
            if callable(reload_fn):
                reload_fn()

    def _on_dimension_filter_field_changed(self) -> None:
        """Re-cascade dimension and measure combos after a dimension field change.

        Internal Logic
        ---------------
        Run :meth:`_sync_dimension_filter_combos` then :meth:`_sync_measure_filter_combos`
        so cross-panel exclusivity stays consistent.
        """

        self._sync_dimension_filter_combos()
        self._sync_measure_filter_combos()

    def _on_measure_filter_field_changed(self) -> None:
        """Re-cascade measure and dimension combos after a measure field change.

        Internal Logic
        ---------------
        Run :meth:`_sync_measure_filter_combos` then :meth:`_sync_dimension_filter_combos`.
        """

        self._sync_measure_filter_combos()
        self._sync_dimension_filter_combos()

    def _make_searchable_checklist_popup(
        self,
        *,
        search_placeholder: str,
        name_supplier: Callable[[], str],
        summary_selected_fmt: str,
    ) -> tuple[QPushButton, QLineEdit, QListWidget, Callable[[], None], Callable[[], None]]:
        """Build a dropdown-style button whose popup holds search plus a checklist.

        Purpose
        -------
        Offer a compact filter control for the Data Grid tab: the main row stays
        one line tall while value or range selection happens in a floating panel
        with a filter line edit and checkable list items (multi-select).

        Internal Logic
        ---------------
        - Create anchor ``QPushButton``, ``QLineEdit`` search, and ``QListWidget``.
        - Parent a ``QFrame`` with ``Qt.Popup`` on this tab; lay out search then list
          with bounded list height so the popup stays scrollable but short.
        - ``refresh_summary`` counts checked rows; with zero checks the anchor shows
          ``{name} Filter`` from ``name_supplier``, else ``summary_selected_fmt`` with ``{n}``.
        - A row of **Select All** / **Deselect All** buttons toggles every non-hidden
          list row (Select All) or every row (Deselect All), matching common filtered
          checklist UX.
        - Install :class:`_AppMousePopupCloser` on ``QApplication`` to hide the popup
          when the user clicks outside the popup and outside the anchor.

        Example invocation
        --------------------
        ``btn, search, lst, upd, hide = self._make_searchable_checklist_popup(``
        ``    search_placeholder='Search…',``
        ``    name_supplier=lambda: combo.currentText(),``
        ``    summary_selected_fmt='{n} picked',``
        ``)``

        Args:
            search_placeholder: Hint text for the popup search field.
            name_supplier: Callable returning the active field name for the ``… Filter`` caption.
            summary_selected_fmt: Anchor label when ``n`` items are checked (must contain ``{n}``).

        Returns:
            ``(drop_button, search_edit, list_widget, refresh_summary_fn, hide_popup_fn)``
        """

        drop_btn = QPushButton(f"{name_supplier()} Filter")
        drop_btn.setMinimumHeight(26)
        drop_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)  # type: ignore[attr-defined]

        search_edit = QLineEdit()
        search_edit.setPlaceholderText(search_placeholder)

        list_widget = QListWidget()
        # Interaction is via checkboxes only (clearer than row selection highlighting).
        list_widget.setSelectionMode(QListWidget.NoSelection)  # type: ignore[attr-defined]

        select_all_btn = QPushButton("Select All")
        deselect_all_btn = QPushButton("Deselect All")

        popup = QFrame(self)
        popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)  # type: ignore[attr-defined]
        try:
            popup.setFrameShape(QFrame.StyledPanel)  # type: ignore[attr-defined]
        except Exception:
            pass
        pop_layout = QVBoxLayout(popup)
        pop_layout.setContentsMargins(6, 6, 6, 6)
        pop_layout.setSpacing(4)
        pop_layout.addWidget(search_edit)
        pop_layout.addWidget(list_widget, 1)
        list_widget.setMinimumHeight(100)
        list_widget.setMaximumHeight(220)
        list_widget.setMinimumWidth(260)

        bulk_row = QHBoxLayout()
        bulk_row.setSpacing(6)
        bulk_row.addWidget(select_all_btn, 1)
        bulk_row.addWidget(deselect_all_btn, 1)
        pop_layout.addLayout(bulk_row)

        def refresh_summary() -> None:
            """Recompute the anchor label from the checklist check states."""

            n: int = sum(
                1
                for i in range(list_widget.count())
                if list_widget.item(i).checkState() == Qt.Checked  # type: ignore[attr-defined]
            )
            field_name = name_supplier()
            drop_btn.setText(summary_selected_fmt.format(n=n) if n else f"{field_name} Filter")

        def select_all_non_hidden() -> None:
            """Check every list row that is not hidden by the search filter."""

            list_widget.blockSignals(True)
            for i in range(list_widget.count()):
                it = list_widget.item(i)
                if not it.isHidden():
                    it.setCheckState(Qt.Checked)  # type: ignore[attr-defined]
            list_widget.blockSignals(False)
            refresh_summary()

        def deselect_all_rows() -> None:
            """Uncheck every row in the list (including rows hidden by search)."""

            list_widget.blockSignals(True)
            for i in range(list_widget.count()):
                list_widget.item(i).setCheckState(Qt.Unchecked)  # type: ignore[attr-defined]
            list_widget.blockSignals(False)
            refresh_summary()

        select_all_btn.clicked.connect(select_all_non_hidden)  # type: ignore[attr-defined]
        deselect_all_btn.clicked.connect(deselect_all_rows)  # type: ignore[attr-defined]

        def hide_popup() -> None:
            """Hide the floating checklist without toggling."""

            popup.hide()

        def toggle_popup() -> None:
            """Show or hide the popup under the anchor; focus search when opening."""

            if popup.isVisible():
                hide_popup()
                return
            anchor = drop_btn
            popup.setFixedWidth(max(280, int(anchor.width())))
            popup.adjustSize()
            popup.move(anchor.mapToGlobal(QPoint(0, int(anchor.height()))))  # type: ignore[attr-defined]
            popup.show()
            popup.raise_()
            search_edit.setFocus()

        drop_btn.clicked.connect(toggle_popup)  # type: ignore[attr-defined]

        closer = _AppMousePopupCloser(popup, drop_btn)
        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.installEventFilter(closer)  # type: ignore[attr-defined]

        return drop_btn, search_edit, list_widget, refresh_summary, hide_popup

    def _remove_dimension_filter_row(self, container: dict[str, object]) -> None:
        """Remove one dimension filter row from the layout and resync combos.

        Purpose
        -------
        Let the user drop a dimension filter row entirely so the panel stays tidy
        when fewer than the maximum number of filters is needed.

        Internal Logic
        ---------------
        - Call the row's ``hide_popup`` hook if present so no orphan popup stays open.
        - Remove the row dict from ``_dimension_filter_rows`` and detach its widget
          from ``_dimension_rows_container`` with ``deleteLater``.
        - Re-run dimension and measure cascade so blocked field names free up.

        Example invocation
        --------------------
        Bound to a per-row **Remove** button: ``self._remove_dimension_filter_row(row_dict)``.

        Args:
            container: The same dict object stored in ``_dimension_filter_rows`` for this row.
        """

        if container not in self._dimension_filter_rows:
            return
        hide_fn = container.get("hide_popup")
        if callable(hide_fn):
            hide_fn()
        row_widget = cast(QWidget, container["widget"])
        self._dimension_filter_rows.remove(container)
        self._dimension_rows_container.removeWidget(row_widget)
        row_widget.setParent(None)
        row_widget.deleteLater()
        self._sync_dimension_filter_combos()
        self._sync_measure_filter_combos()

    def _remove_measure_filter_row(self, container: dict[str, object]) -> None:
        """Remove one measure filter row from the layout and resync combos.

        Purpose
        -------
        Same as :meth:`_remove_dimension_filter_row`, but for the measure filter panel.

        Internal Logic
        ---------------
        Hide popup, remove from ``_measure_filter_rows``, detach widget, then
        :meth:`_sync_measure_filter_combos` and :meth:`_sync_dimension_filter_combos`.

        Example invocation
        --------------------
        ``self._remove_measure_filter_row(container)`` from a **Remove** button slot.

        Args:
            container: The dict stored in ``_measure_filter_rows`` for this row.
        """

        if container not in self._measure_filter_rows:
            return
        hide_fn = container.get("hide_popup")
        if callable(hide_fn):
            hide_fn()
        row_widget = cast(QWidget, container["widget"])
        self._measure_filter_rows.remove(container)
        self._measure_rows_container.removeWidget(row_widget)
        row_widget.setParent(None)
        row_widget.deleteLater()
        self._sync_measure_filter_combos()
        self._sync_dimension_filter_combos()

    def _add_dimension_filter_row(self) -> None:
        """Add a new dimension filter row up to the UI limit."""

        if len(self._dimension_filter_rows) >= 5:
            return
        self._create_dimension_filter_row()

    def _add_measure_filter_row(self) -> None:
        """Add a new measure filter row up to the UI limit."""

        if len(self._measure_filter_rows) >= 5:
            return
        self._create_measure_filter_row()

    def _create_dimension_filter_row(self) -> None:
        """Create one dimension filter row (field dropdown + multi-select values)."""

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 0, 2)
        row_layout.setSpacing(8)

        field_combo = QComboBox()
        for name in self._available_dimensions():
            field_combo.addItem(name)
        field_combo.setCurrentIndex(0)

        dim_filter_btn, value_search, values_list, refresh_summary, hide_filter_popup = (
            self._make_searchable_checklist_popup(
                search_placeholder="Search dimension values…",
                name_supplier=lambda: field_combo.currentText(),
                summary_selected_fmt="{n} value(s) selected",
            )
        )
        dim_filter_btn.setToolTip(
            "Open to search, use Select All / Deselect All, and multi-select dimension values."
        )

        remove_btn = QPushButton("Remove")
        remove_btn.setToolTip("Remove this dimension filter row from the panel.")

        container: dict[str, object] = {}

        def remove_this_row() -> None:
            """Remove this row using the shared container dict reference."""

            self._remove_dimension_filter_row(container)

        def reload_values() -> None:
            """Reload unique values for the currently selected dimension.

            Purpose
            -------
            Fetch distinct strings for the active dimension column and repopulate
            the checklist while preserving checked values per column name when the
            user switches the field combo away and back.

            Internal Logic
            ---------------
            - Query DuckDB for distinct ``CAST(col AS VARCHAR)`` ordered values.
            - After a successful query, copy currently checked ``UserRole`` values
              into ``dim_value_checks[last_dim_col]`` (the column the list still
              represents) before clearing.
            - Rebuild rows; restore checks from ``dim_value_checks[col]``; set
              ``last_dim_col`` to ``col``.

            Example invocation
            --------------------
            ``reload_values()`` from :meth:`_sync_dimension_filter_combos` after the
            combo text changes.
            """

            import duckdb  # type: ignore

            col = field_combo.currentText()
            try:
                conn = duckdb.connect(database=str(self._ctx.database_path))
                try:
                    sql = f"SELECT DISTINCT CAST({col} AS VARCHAR) AS v FROM {self._ctx.table_name} ORDER BY v;"
                    uniques = conn.execute(sql).fetchall()
                finally:
                    conn.close()
            except Exception as e:
                QMessageBox.critical(self, "Value load failed", str(e))
                return

            saved = cast(dict[str, set[str]], container["dim_value_checks"])
            last_o = container.get("last_dim_col")
            last_col: Optional[str] = last_o if isinstance(last_o, str) else None
            if last_col is not None and values_list.count() > 0:
                picked: set[str] = set()
                for i in range(values_list.count()):
                    it = values_list.item(i)
                    if it.checkState() == Qt.Checked:  # type: ignore[attr-defined]
                        picked.add(str(it.data(Qt.UserRole)))
                saved[last_col] = picked

            restore: set[str] = set(saved.get(col, set()))
            values_list.blockSignals(True)
            values_list.clear()
            for (v,) in uniques:
                sv = str(v)
                item = QListWidgetItem(sv)
                item.setData(Qt.UserRole, sv)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if sv in restore else Qt.Unchecked)  # type: ignore[attr-defined]
                values_list.addItem(item)
            values_list.blockSignals(False)
            container["last_dim_col"] = col
            apply_value_search()
            refresh_summary()

        def apply_value_search() -> None:
            """Hide values not matching the search text."""

            q = value_search.text().strip().lower()
            for i in range(values_list.count()):
                it = values_list.item(i)
                label = it.text().lower()
                it.setHidden(bool(q) and q not in label)

        values_list.itemChanged.connect(lambda *_: refresh_summary())  # type: ignore[attr-defined]
        value_search.textChanged.connect(apply_value_search)  # type: ignore[attr-defined]

        row_layout.addWidget(field_combo, 2)
        row_layout.addWidget(dim_filter_btn, 3)
        row_layout.addWidget(remove_btn, 0)

        remove_btn.clicked.connect(remove_this_row)  # type: ignore[attr-defined]

        container.update(
            {
                "widget": row_widget,
                "field_combo": field_combo,
                "values_list": values_list,
                "reload_values": reload_values,
                "hide_popup": hide_filter_popup,
                "dim_value_checks": {},
                "last_dim_col": None,
            }
        )
        self._dimension_rows_container.addWidget(row_widget)
        self._dimension_filter_rows.append(container)
        field_combo.currentIndexChanged.connect(hide_filter_popup)  # type: ignore[attr-defined]
        field_combo.currentIndexChanged.connect(lambda *_: self._on_dimension_filter_field_changed())  # type: ignore[attr-defined]
        field_combo.currentIndexChanged.connect(lambda *_: refresh_summary())  # type: ignore[attr-defined]
        self._sync_dimension_filter_combos()
        refresh_summary()

    def _create_measure_filter_row(self) -> None:
        """Create one measure filter row (field dropdown + multi-select numeric ranges)."""

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 0, 2)
        row_layout.setSpacing(8)

        field_combo = QComboBox()
        for name in self._available_measures():
            field_combo.addItem(name)
        field_combo.setCurrentIndex(0)

        range_filter_btn, range_search, ranges_list, refresh_range_summary, hide_range_popup = (
            self._make_searchable_checklist_popup(
                search_placeholder="Search measure ranges…",
                name_supplier=lambda: field_combo.currentText(),
                summary_selected_fmt="{n} range(s) selected",
            )
        )
        range_filter_btn.setToolTip(
            "Open to search, use Select All / Deselect All, and multi-select numeric range buckets."
        )

        remove_btn = QPushButton("Remove")
        remove_btn.setToolTip("Remove this measure filter row from the panel.")

        container: dict[str, object] = {}

        def remove_this_row() -> None:
            """Remove this row using the shared container dict reference."""

            self._remove_measure_filter_row(container)

        # Store the computed range specs for this row.
        ranges_specs: list[RangeSpec] = []

        def compute_and_reload_ranges() -> None:
            """Compute 10 equal-width ranges from min/max and reload checkboxes.

            Purpose
            -------
            Build ten equal-width numeric buckets for the active measure and refresh
            the checklist, keeping prior bucket selections per measure name when the
            user changes the field combo.

            Internal Logic
            ---------------
            - Read ``MIN``/``MAX`` for ``col`` from DuckDB; bail on error or null span.
            - Build ten :class:`RangeSpec` rows (last bucket inclusive on the high end).
            - Before clearing the list, store checked row indices under
              ``meas_row_checks[last_meas_col]``.
            - Repopulate; restore checks whose index is in ``meas_row_checks[col]``;
              set ``last_meas_col`` to ``col``.

            Example invocation
            --------------------
            ``compute_and_reload_ranges()`` from :meth:`_sync_measure_filter_combos`.
            """

            import duckdb  # type: ignore

            col = field_combo.currentText()
            try:
                conn = duckdb.connect(database=str(self._ctx.database_path))
                try:
                    min_val, max_val = conn.execute(
                        f"SELECT MIN({col}), MAX({col}) FROM {self._ctx.table_name}"
                    ).fetchone()
                finally:
                    conn.close()
            except Exception as e:
                QMessageBox.critical(self, "Range load failed", str(e))
                return

            if min_val is None or max_val is None:
                QMessageBox.warning(self, "No numeric range", f"No min/max for {col}.")
                return

            minf = float(min_val)
            maxf = float(max_val)
            if minf == maxf:
                # Degenerate: create 10 identical ranges.
                step = 1.0
            else:
                step = (maxf - minf) / 10.0

            dp = max(0, min(30, int(self._ctx.measure_decimal_places)))
            new_specs: list[RangeSpec] = []
            for i in range(10):
                start = minf + i * step
                end = minf + (i + 1) * step
                include_end = i == 9
                label = f"between {start:,.{dp}f} and {end:,.{dp}f}"
                new_specs.append(RangeSpec(start=start, end=end, label=label, include_end=include_end))

            nonlocal ranges_specs
            ranges_specs = new_specs

            saved_m = cast(dict[str, set[int]], container["meas_row_checks"])
            last_m_o = container.get("last_meas_col")
            last_m: Optional[str] = last_m_o if isinstance(last_m_o, str) else None
            if last_m is not None and ranges_list.count() > 0:
                picked_i: set[int] = {
                    i
                    for i in range(ranges_list.count())
                    if ranges_list.item(i).checkState() == Qt.Checked  # type: ignore[attr-defined]
                }
                saved_m[last_m] = picked_i

            restore_i: set[int] = set(saved_m.get(col, set()))
            ranges_list.blockSignals(True)
            ranges_list.clear()
            for i, spec in enumerate(ranges_specs):
                label = f"between {spec.start:,.{dp}f} and {spec.end:,.{dp}f}"
                item = QListWidgetItem(label)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if i in restore_i else Qt.Unchecked)  # type: ignore[attr-defined]
                item.setData(Qt.UserRole, spec)
                ranges_list.addItem(item)
            ranges_list.blockSignals(False)
            container["last_meas_col"] = col
            apply_range_search()
            refresh_range_summary()

        def apply_range_search() -> None:
            """Hide range items not matching search."""

            q = range_search.text().strip().lower()
            for i in range(ranges_list.count()):
                it = ranges_list.item(i)
                it.setHidden(bool(q) and q not in it.text().lower())

        ranges_list.itemChanged.connect(lambda *_: refresh_range_summary())  # type: ignore[attr-defined]
        range_search.textChanged.connect(apply_range_search)  # type: ignore[attr-defined]

        row_layout.addWidget(field_combo, 2)
        row_layout.addWidget(range_filter_btn, 3)
        row_layout.addWidget(remove_btn, 0)

        remove_btn.clicked.connect(remove_this_row)  # type: ignore[attr-defined]

        container.update(
            {
                "widget": row_widget,
                "field_combo": field_combo,
                "ranges_list": ranges_list,
                "ranges_specs_ref": ranges_specs,
                "reload_ranges": compute_and_reload_ranges,
                "hide_popup": hide_range_popup,
                "meas_row_checks": {},
                "last_meas_col": None,
            }
        )
        # Note: ranges_specs_ref is informational; actual specs are stored in closure.
        self._measure_rows_container.addWidget(row_widget)
        self._measure_filter_rows.append(container)
        field_combo.currentIndexChanged.connect(hide_range_popup)  # type: ignore[attr-defined]
        field_combo.currentIndexChanged.connect(lambda *_: self._on_measure_filter_field_changed())  # type: ignore[attr-defined]
        field_combo.currentIndexChanged.connect(lambda *_: refresh_range_summary())  # type: ignore[attr-defined]
        self._sync_measure_filter_combos()
        refresh_range_summary()

    def _on_apply_filters_clicked(self) -> None:
        """Build user filters WHERE clause, clear chart click filter, and refresh."""

        self._active_mode = "user"
        self._chart_click_filter_where_sql = ""
        self._chart_click_filter_human_summary = ""
        self._user_filters_where_sql = self._build_user_filters_where_sql()
        self._applied_panel_filter_summary = self._build_panel_filter_human_summary()
        self._page_index = 0
        self._update_pager_context_label()
        self._refresh_surface_async()

    def _clear_panel_filter_ui_selections(self) -> None:
        """Uncheck all dimension value and measure range selections in the filter panel.

        Purpose
        -------
        Reset the filter panel widgets before clearing stored WHERE state so the UI
        matches an unfiltered dataset.

        Internal Logic
        ----------------
        For each dimension row, uncheck every value in the checklist and clear
        ``dim_value_checks``. For each measure row, uncheck range buckets and clear
        ``meas_row_checks``.
        """

        for row in self._dimension_filter_rows:
            values_list = row.get("values_list")
            if isinstance(values_list, QListWidget):
                values_list.blockSignals(True)
                for i in range(values_list.count()):
                    values_list.item(i).setCheckState(Qt.Unchecked)  # type: ignore[attr-defined]
                values_list.blockSignals(False)
            saved = row.get("dim_value_checks")
            if isinstance(saved, dict):
                saved.clear()

        for row in self._measure_filter_rows:
            ranges_list = row.get("ranges_list")
            if isinstance(ranges_list, QListWidget):
                ranges_list.blockSignals(True)
                for i in range(ranges_list.count()):
                    ranges_list.item(i).setCheckState(Qt.Unchecked)  # type: ignore[attr-defined]
                ranges_list.blockSignals(False)
            saved = row.get("meas_row_checks")
            if isinstance(saved, dict):
                saved.clear()

    def _on_clear_filters_clicked(self) -> None:
        """Clear user-applied filters (panel, chart-click, and live column quick filters).

        Purpose
        -------
        Let users reset all filter state they applied without reloading the dataset.

        Internal Logic
        ----------------
        1. Uncheck dimension/measure panel selections via :meth:`_clear_panel_filter_ui_selections`.
        2. Clear stored user/chart WHERE clauses and the applied-filter summary string.
        3. On the Data Grid surface, clear per-column header filter line edits.
        4. Reset to page 0 and refresh the active surface (grid or pivot).

        Example invocation
        --------------------
        Bound to the **Clear Filters** toolbar button next to **Apply Filters**.
        """

        self._clear_panel_filter_ui_selections()
        self._chart_click_filter_where_sql = ""
        self._chart_click_filter_human_summary = ""
        self._user_filters_where_sql = ""
        self._applied_panel_filter_summary = ""
        self._active_mode = "none"

        if self._ui_surface == "grid":
            try:
                self._live_column_filter_timer.stop()  # type: ignore[attr-defined]
            except Exception:
                pass
            for edit in self._column_filter_inputs:
                edit.blockSignals(True)
                edit.clear()
                edit.blockSignals(False)

        self._page_index = 0
        self._update_pager_context_label()
        self._refresh_surface_async()

    def _build_user_filters_where_sql(self) -> str:
        """Build WHERE clause from current dimension/measure filter selections."""

        dim_predicates: list[str] = []
        meas_predicates: list[str] = []

        # Dimensions: IN (selected values).
        for row in self._dimension_filter_rows:
            field_combo = row["field_combo"]
            values_list = row["values_list"]
            assert isinstance(field_combo, QComboBox)
            assert isinstance(values_list, QListWidget)

            col = field_combo.currentText()
            selected: list[str] = []
            for i in range(values_list.count()):
                it = values_list.item(i)
                if it.checkState() == Qt.Checked:
                    selected.append(it.data(Qt.UserRole))

            if selected:
                values_sql = ", ".join(_sql_quote(v) for v in selected)
                dim_predicates.append(f"{col} IN ({values_sql})")

        # Measures: OR over selected ranges.
        for row in self._measure_filter_rows:
            field_combo = row["field_combo"]
            ranges_list = row["ranges_list"]
            assert isinstance(field_combo, QComboBox)
            assert isinstance(ranges_list, QListWidget)

            col = field_combo.currentText()
            range_predicates: list[str] = []
            for i in range(ranges_list.count()):
                it = ranges_list.item(i)
                if it.checkState() != Qt.Checked:
                    continue
                spec = it.data(Qt.UserRole)
                assert isinstance(spec, RangeSpec)
                if spec.include_end:
                    range_predicates.append(f"{col} >= {spec.start} AND {col} <= {spec.end}")
                else:
                    range_predicates.append(f"{col} >= {spec.start} AND {col} < {spec.end}")

            if range_predicates:
                meas_predicates.append("(" + " OR ".join(range_predicates) + ")")

        # Combine all with AND.
        predicates = dim_predicates + meas_predicates
        return " AND ".join(predicates)

    def set_chart_click_filter(self, where_sql: str, *, human_summary: str = "") -> None:
        """Receive chart-click WHERE clause from the Visualize tab.

        Purpose
        -------
        Update the grid so it shows the data underlying the clicked chart mark.

        Internal Logic
        ---------------
        - Store the WHERE expression (without a leading ``WHERE`` keyword).
        - Store ``human_summary`` (for example ``NAME in (United States)``) for the pager.
        - Switch active mode to ``chart``.
        - Refresh the first page so the user sees immediate results.

        Example invocation
        --------------------
        ``set_chart_click_filter("NAME = 'US'", human_summary="NAME in (US)")``
        """

        self._chart_click_filter_where_sql = where_sql or ""
        if self._chart_click_filter_where_sql:
            self._chart_click_filter_human_summary = human_summary.strip()
            self._active_mode = "chart"
            self._page_index = 0
            self._refresh_surface_async()
        else:
            self._chart_click_filter_human_summary = ""
            # Clear chart filter only when it is empty.
            if self._active_mode == "chart":
                self._active_mode = "none"
                self._page_index = 0
                self._refresh_surface_async()

    def export_reload_session_state(self) -> Dict[str, object]:
        """Capture filter, sort, pagination, pivot shelf, and header-filter state for reload.

        Purpose
        -------
        After the user changes zoom, ``Load Data`` rebuilds tabs. This snapshot lets
        :meth:`import_reload_session_state` restore dimension/measure filter rows,
        chart vs user filter mode, grid header quick-filters, and pivot field shelves.

        Internal Logic
        ----------------
        Serialize dimension rows (field + checked value strings), measure rows
        (field + checked bucket indices), active WHERE mode, optional live header
        filter texts keyed by column name, and pivot selections when
        ``self._ui_surface == \"pivot\"``.

        Example invocation
        --------------------
        ``snap = grid_tab.export_reload_session_state()``
        """

        dim_snap: list[dict[str, object]] = []
        for row in self._dimension_filter_rows:
            fc = row["field_combo"]
            vl = row["values_list"]
            assert isinstance(fc, QComboBox)
            assert isinstance(vl, QListWidget)
            vals: list[str] = []
            for i in range(vl.count()):
                it = vl.item(i)
                if it.checkState() == Qt.Checked:  # type: ignore[attr-defined]
                    vals.append(str(it.data(Qt.UserRole)))  # type: ignore[attr-defined]
            dim_snap.append({"field": fc.currentText(), "values": vals})

        meas_snap: list[dict[str, object]] = []
        for row in self._measure_filter_rows:
            fc = row["field_combo"]
            rl = row["ranges_list"]
            assert isinstance(fc, QComboBox)
            assert isinstance(rl, QListWidget)
            idxs = [
                i
                for i in range(rl.count())
                if rl.item(i).checkState() == Qt.Checked  # type: ignore[attr-defined]
            ]
            meas_snap.append({"field": fc.currentText(), "buckets": idxs})

        live_hdr: dict[str, str] = {}
        if self._ui_surface == "grid" and self._last_grid_columns:
            for j, name in enumerate(self._last_grid_columns):
                if j < len(self._column_filter_inputs):
                    t = self._column_filter_inputs[j].text().strip()
                    if t:
                        live_hdr[name] = t

        out: Dict[str, object] = {
            "surface": self._ui_surface,
            "dimension_filters": dim_snap,
            "measure_filters": meas_snap,
            "active_mode": self._active_mode,
            "chart_click_filter_where_sql": self._chart_click_filter_where_sql,
            "chart_click_filter_human_summary": self._chart_click_filter_human_summary,
            "user_filters_where_sql": self._user_filters_where_sql,
            "applied_panel_filter_summary": self._applied_panel_filter_summary,
            "page_size": int(self._page_size),
            "page_index": int(self._page_index),
        }
        if self._ui_surface == "grid":
            out["sort_by_name"] = bool(self._sort_columns_checkbox.isChecked())
            out["sort_specs"] = list(self._grid_sort_specs)
            out["visible_columns"] = sorted(self._visible_columns_set)
            out["live_header_filters"] = live_hdr
        if self._ui_surface == "pivot":
            out["pivot_rows"] = self._pivot_selected_in_order(self._pivot_rows_list, self._available_dimensions())
            out["pivot_cols"] = self._pivot_selected_in_order(self._pivot_cols_list, self._available_dimensions())
            out["pivot_measures"] = self._pivot_selected_in_order(self._pivot_vals_list, self._available_measures())
            out["pivot_agg"] = self._pivot_agg_combo.currentText()
        return out

    def import_reload_session_state(self, data: Optional[Dict[str, object]]) -> None:
        """Restore state from :meth:`export_reload_session_state` after tabs are rebuilt.

        Purpose
        -------
        Re-apply the user's filter rows, selections, and mode after ``Load Data`` so
        a zoom-driven reload matches the prior session.

        Internal Logic
        ----------------
        Match dimension/measure row counts, repopulate combos and checklists via each
        row's ``reload_values`` / ``reload_ranges``, restore pivot shelf when on the
        pivot surface, then re-activate chart or user filter mode and refresh.

        Example invocation
        --------------------
        ``grid_tab.import_reload_session_state(snap)``
        """

        if not data or not isinstance(data, dict):
            return
        if str(data.get("surface", "")) != self._ui_surface:
            return

        hdr = data.get("live_header_filters")
        if isinstance(hdr, dict) and self._ui_surface == "grid":
            self._session_header_filter_texts = {str(k): str(v) for k, v in hdr.items()}
        else:
            self._session_header_filter_texts = {}

        dim_snap = data.get("dimension_filters")
        if isinstance(dim_snap, list):
            self._resize_filter_row_list(
                dim_snap,
                self._dimension_filter_rows,
                self._add_dimension_filter_row,
                self._remove_dimension_filter_row,
            )
            for i, spec in enumerate(dim_snap):
                if i >= len(self._dimension_filter_rows) or not isinstance(spec, dict):
                    continue
                row = self._dimension_filter_rows[i]
                fc = cast(QComboBox, row["field_combo"])
                field = str(spec.get("field", ""))
                idx = fc.findText(field)
                if idx >= 0:
                    fc.setCurrentIndex(idx)
                reload_fn = row.get("reload_values")
                if callable(reload_fn):
                    reload_fn()
                vl = cast(QListWidget, row["values_list"])
                want = {str(x) for x in spec.get("values", []) if isinstance(x, (str, int, float))}
                vl.blockSignals(True)
                for j in range(vl.count()):
                    it = vl.item(j)
                    sv = str(it.data(Qt.UserRole))  # type: ignore[attr-defined]
                    it.setCheckState(Qt.Checked if sv in want else Qt.Unchecked)  # type: ignore[attr-defined]
                vl.blockSignals(False)

        meas_snap = data.get("measure_filters")
        if isinstance(meas_snap, list):
            self._resize_filter_row_list(
                meas_snap,
                self._measure_filter_rows,
                self._add_measure_filter_row,
                self._remove_measure_filter_row,
            )
            for i, spec in enumerate(meas_snap):
                if i >= len(self._measure_filter_rows) or not isinstance(spec, dict):
                    continue
                row = self._measure_filter_rows[i]
                fc = cast(QComboBox, row["field_combo"])
                field = str(spec.get("field", ""))
                idx = fc.findText(field)
                if idx >= 0:
                    fc.setCurrentIndex(idx)
                reload_fn = row.get("reload_ranges")
                if callable(reload_fn):
                    reload_fn()
                rl = cast(QListWidget, row["ranges_list"])
                raw_b = spec.get("buckets", [])
                want_i: set[int] = set()
                for x in raw_b:
                    try:
                        want_i.add(int(x))
                    except (TypeError, ValueError):
                        pass
                rl.blockSignals(True)
                for j in range(rl.count()):
                    it = rl.item(j)
                    it.setCheckState(Qt.Checked if j in want_i else Qt.Unchecked)  # type: ignore[attr-defined]
                rl.blockSignals(False)

        if self._ui_surface == "grid":
            try:
                self._sort_columns_checkbox.setChecked(bool(data.get("sort_by_name")))
            except Exception:
                pass
            specs = data.get("sort_specs")
            if isinstance(specs, list):
                parsed: list[tuple[str, bool]] = []
                for ent in specs:
                    if isinstance(ent, (list, tuple)) and len(ent) == 2:
                        parsed.append((str(ent[0]), bool(ent[1])))
                self._grid_sort_specs = parsed
            vis = data.get("visible_columns")
            if isinstance(vis, list):
                names = {str(x) for x in vis}
                allowed = {f.name for f in self._ctx.meta.fields}
                self._visible_columns_set = {n for n in names if n in allowed}
                if not self._visible_columns_set:
                    self._visible_columns_set = allowed.copy()
                self._rebuild_drawer_list()

        try:
            self._page_size = max(1, int(data.get("page_size", self._page_size)))
        except (TypeError, ValueError):
            pass
        try:
            self._page_index = max(0, int(data.get("page_index", 0)))
        except (TypeError, ValueError):
            pass

        if self._ui_surface == "pivot":
            self._apply_list_selection_by_names(self._pivot_rows_list, self._available_dimensions(), data.get("pivot_rows"))
            self._apply_list_selection_by_names(self._pivot_cols_list, self._available_dimensions(), data.get("pivot_cols"))
            self._apply_list_selection_by_names(self._pivot_vals_list, self._available_measures(), data.get("pivot_measures"))
            agg = data.get("pivot_agg")
            if isinstance(agg, str):
                ai = self._pivot_agg_combo.findText(agg)
                if ai >= 0:
                    self._pivot_agg_combo.setCurrentIndex(ai)

        mode = str(data.get("active_mode", "none"))
        chart_sql = str(data.get("chart_click_filter_where_sql", "")).strip()
        chart_summary = str(data.get("chart_click_filter_human_summary", "")).strip()
        if mode == "chart" and chart_sql:
            self.set_chart_click_filter(chart_sql, human_summary=chart_summary)
            return
        self._chart_click_filter_where_sql = ""
        self._chart_click_filter_human_summary = ""
        self._user_filters_where_sql = self._build_user_filters_where_sql()
        self._applied_panel_filter_summary = self._build_panel_filter_human_summary()
        self._active_mode = "user" if self._user_filters_where_sql.strip() else "none"
        self._refresh_surface_async()

    def _resize_filter_row_list(
        self,
        target_specs: list[dict[str, object]],
        rows: list[dict[str, object]],
        add_fn: Callable[[], None],
        remove_fn: Callable[[dict[str, object]], None],
    ) -> None:
        """Grow or shrink filter rows to match ``len(target_specs)`` (at least one row)."""

        want = max(1, len(target_specs))
        while len(rows) > want and len(rows) > 1:
            remove_fn(rows[-1])
        while len(rows) < want:
            add_fn()

    def _apply_list_selection_by_names(
        self,
        lst: QListWidget,
        universe: Sequence[str],
        selected: object,
    ) -> None:
        """Clear ``lst`` selection then select names in ``universe`` order."""

        names: list[str] = []
        if isinstance(selected, (list, tuple)):
            names = [str(x) for x in selected]
        sel_set = set(names)
        lst.blockSignals(True)
        lst.clearSelection()
        for n in universe:
            if n not in sel_set:
                continue
            for i in range(lst.count()):
                it = lst.item(i)
                if it.text() == n:
                    it.setSelected(True)
                    break
        lst.blockSignals(False)

    def _current_where_sql(self) -> str:
        """Return the active WHERE clause based on active mode."""

        if self._active_mode == "chart":
            return self._chart_click_filter_where_sql
        if self._active_mode == "user":
            return self._user_filters_where_sql
        return ""

    def _live_column_filters_sql(self) -> str:
        """Build ANDed predicates from the per-column live filter line edits.

        Purpose
        -------
        Each non-empty filter narrows the DuckDB query. **Measure** columns accept
        an inclusive numeric range (for example ``100-400`` or ``100 and 400``)
        resolved via :func:`_parse_measure_range_bounds`; otherwise substring match
        applies. **Dimension** columns always use case-insensitive substring match.

        Internal Logic
        ---------------
        For each visible column, resolve metadata dtype. Measures: if
        ``_parse_measure_range_bounds`` returns bounds, append a ``try_cast`` double
        ``BETWEEN`` predicate; else append ``instr`` on varchar. Non-measures: append
        ``instr`` only. Identifiers use :func:`_grid_sql_ident`.

        Example invocation
        --------------------
        Used by :meth:`_combined_where_sql_for_query` when building the worker
        ``where_sql`` string.
        """

        parts: list[str] = []
        if not self._column_filter_inputs or not self._last_grid_columns:
            return ""
        for i, edit in enumerate(self._column_filter_inputs):
            if i >= len(self._last_grid_columns):
                break
            raw = edit.text().strip()
            if not raw:
                continue
            col = self._last_grid_columns[i]
            iq = _grid_sql_ident(col)
            field = self._ctx.meta.fields_by_name.get(col)
            if field is not None and field_formats_as_measure(field):
                mbounds = _parse_measure_range_bounds(raw)
                if mbounds is not None:
                    lo, hi = mbounds
                    try:
                        lo_lit = _sql_double_literal(lo)
                        hi_lit = _sql_double_literal(hi)
                    except ValueError:
                        pass
                    else:
                        parts.append(
                            f"(try_cast({iq} AS DOUBLE) BETWEEN {lo_lit} AND {hi_lit})"
                        )
                        continue
            lit = raw.replace("'", "''")
            parts.append(f"instr(LOWER(CAST({iq} AS VARCHAR)), LOWER('{lit}')) > 0")
        return " AND ".join(parts)

    def _combined_where_sql_for_query(self) -> str:
        """Merge chart/user WHERE with live per-column text filters."""

        if self._ui_surface == "pivot":
            return self._current_where_sql().strip()

        base = self._current_where_sql().strip()
        live = self._live_column_filters_sql().strip()
        chunks: list[str] = []
        if base:
            chunks.append(f"({base})")
        if live:
            chunks.append(f"({live})")
        return " AND ".join(chunks)

    def _build_panel_filter_human_summary(self) -> str:
        """Describe dimension and measure panel selections in plain language.

        Purpose
        -------
        Produce text like ``Name in (Alabama,Texas)`` or ``POP in (3 numeric ranges)`` for
        the pager strip, matching the semantics of :meth:`_build_user_filters_where_sql`.

        Internal Logic
        ---------------
        - Dimensions: gather checked ``Qt.UserRole`` values, join with comma only
          (no space), as in ``STATE in (01,02)``.
        - Measures: collect checked :class:`RangeSpec` entries; one range uses a
          shortened ``label``, several collapse to a count phrase.

        Example invocation
        --------------------
        Called when the user clicks **Apply Filters** to snapshot the summary string.

        Returns:
            Semicolon-separated clauses, or an empty string when nothing is selected.
        """

        parts: list[str] = []
        for row in self._dimension_filter_rows:
            field_combo = row["field_combo"]
            values_list = row["values_list"]
            assert isinstance(field_combo, QComboBox)
            assert isinstance(values_list, QListWidget)
            col = field_combo.currentText()
            selected: list[str] = []
            for i in range(values_list.count()):
                it = values_list.item(i)
                if it.checkState() == Qt.Checked:
                    raw = it.data(Qt.UserRole)
                    selected.append(str(raw))
            if selected:
                vals = ",".join(selected)
                parts.append(f"{col} in ({vals})")

        for row in self._measure_filter_rows:
            field_combo = row["field_combo"]
            ranges_list = row["ranges_list"]
            assert isinstance(field_combo, QComboBox)
            assert isinstance(ranges_list, QListWidget)
            col = field_combo.currentText()
            checked_specs: list[RangeSpec] = []
            for i in range(ranges_list.count()):
                it = ranges_list.item(i)
                if it.checkState() != Qt.Checked:
                    continue
                spec = it.data(Qt.UserRole)
                if isinstance(spec, RangeSpec):
                    checked_specs.append(spec)
            if not checked_specs:
                continue
            if len(checked_specs) == 1:
                short = checked_specs[0].label
                if len(short) > 52:
                    short = f"{short[:49]}..."
                parts.append(f"{col} in ({short})")
            else:
                parts.append(f"{col} in ({len(checked_specs)} numeric ranges)")

        return "; ".join(parts)

    def _describe_live_column_filters_line(self) -> str:
        """Summarize non-empty per-column header filter boxes.

        Purpose
        -------
        Mirror the intent of :meth:`_live_column_filters_sql` in user-readable phrases
        such as ``NAME contains "Al"`` or ``POP between 100 and 400``.

        Internal Logic
        ---------------
        Zip :attr:`_last_grid_columns` with :attr:`_column_filter_inputs`, skip blanks,
        and branch on measure numeric range parsing vs substring wording.

        Returns:
            Semicolon-separated phrases, or empty when no live filters are set.
        """

        if self._ui_surface == "pivot":
            return ""
        if not self._last_grid_columns:
            return ""
        chunks: list[str] = []
        for i, col in enumerate(self._last_grid_columns):
            if i >= len(self._column_filter_inputs):
                break
            raw = self._column_filter_inputs[i].text().strip()
            if not raw:
                continue
            meta = self._ctx.meta.fields_by_name.get(col)
            if meta is not None and field_formats_as_measure(meta):
                mbounds = _parse_measure_range_bounds(raw)
                if mbounds is not None:
                    lo, hi = mbounds
                    dp = max(0, min(30, int(self._ctx.measure_decimal_places)))
                    chunks.append(f"{col} between {lo:,.{dp}f} and {hi:,.{dp}f}")
                    continue
            safe = raw.replace('"', "'")
            chunks.append(f'{col} contains "{safe}"')
        return "; ".join(chunks)

    def _truncate_pager_block(self, full: str, max_len: int = 260) -> tuple[str, str]:
        """Return display text and tooltip; elide display when overly long."""

        if len(full) <= max_len:
            return full, full
        return f"{full[: max_len - 1]}…", full

    def _format_pivot_selection_summary_line(self) -> str:
        """Build the full single-line summary of Rows, Columns, Values, and Aggregation.

        Purpose
        -------
        Shown above the pivot results table so users see the active shelf choices in
        the same style as **Filters - …** in the pager strip.

        Internal Logic
        ---------------
        Read the three pivot ``QListWidget`` selections via :meth:`_pivot_selected_in_order`
        (stable meta order) and the aggregation combo’s current text; join each list
        with commas, using ``(none)`` when empty.

        Example invocation
        --------------------
        ``full = self._format_pivot_selection_summary_line()`` might return
        ``\"Selected Rows: A, B; Columns: (none); Values: POP; Aggregation: SUM\"``.
        """

        row_dims = self._pivot_selected_in_order(self._pivot_rows_list, self._available_dimensions())
        col_dims = self._pivot_selected_in_order(self._pivot_cols_list, self._available_dimensions())
        measures = self._pivot_selected_in_order(self._pivot_vals_list, self._available_measures())
        agg = self._pivot_agg_combo.currentText()

        def fmt(names: list[str]) -> str:
            return ", ".join(names) if names else "(none)"

        return (
            f"Selected Rows: {fmt(row_dims)}; "
            f"Columns: {fmt(col_dims)}; "
            f"Values: {fmt(measures)}; "
            f"Aggregation: {agg}"
        )

    def _update_pivot_selection_summary_label(self) -> None:
        """Paint the pivot shelf summary above the grid (truncate + tooltip like filters).

        Purpose
        -------
        Keep the label under **Expand All** in sync whenever row/column/value selection
        or the aggregation changes, or after a successful pivot refresh.

        Internal Logic
        ---------------
        If not pivot UI or the label is missing, return. Else set text/tooltip from
        :meth:`_format_pivot_selection_summary_line` via :meth:`_truncate_pager_block`.

        Example invocation
        --------------------
        ``self._pivot_rows_list.itemSelectionChanged.connect(self._update_pivot_selection_summary_label)``
        """

        if self._ui_surface != "pivot":
            return
        w = self._pivot_selection_summary_label
        if w is None:
            return
        full = self._format_pivot_selection_summary_line()
        disp, tip = self._truncate_pager_block(full)
        w.setText(disp)
        w.setToolTip(tip)

    def _build_applied_filters_segments(self) -> list[str]:
        """Collect human-readable filter clauses for the pager / pivot summary strip.

        Purpose
        -------
        Shared by the Data Grid pager and the Pivot Data tab so both surfaces show
        the same **Filters - …** wording.

        Internal Logic
        ----------------
        Include chart-linked selection, applied panel filters (user mode), and live
        column header filters (grid only).

        Example invocation
        --------------------
        ``\"; \".join(self._build_applied_filters_segments())``
        """

        segments: list[str] = []
        if self._active_mode == "chart" and self._chart_click_filter_where_sql.strip():
            detail = self._chart_click_filter_human_summary.strip()
            if detail:
                segments.append(f"chart-linked selection ({detail})")
            else:
                segments.append("chart-linked selection")
        if self._active_mode == "user":
            applied = self._applied_panel_filter_summary.strip()
            if applied:
                segments.append(applied)
        live = self._describe_live_column_filters_line().strip()
        if live:
            segments.append(live)
        return segments

    def _format_applied_filters_line(self, *, loading: bool = False) -> str:
        """Return the full **Filters - …** line shown on Grid and Pivot tabs."""

        if loading:
            return "Filters - …"
        segments = self._build_applied_filters_segments()
        return "Filters - " + ("; ".join(segments) if segments else "(none)")

    def _paint_applied_filters_labels(self, full: str) -> None:
        """Set pager (and pivot) filter summary labels with truncation + tooltip."""

        disp, tip = self._truncate_pager_block(full)
        self._pager_summary_label.setText(disp)
        self._pager_summary_label.setToolTip(tip)
        if self._ui_surface == "pivot" and self._pivot_filters_summary_label is not None:
            self._pivot_filters_summary_label.setText(disp)
            self._pivot_filters_summary_label.setToolTip(tip)

    def _update_pager_context_label(self, *, loading: bool = False) -> None:
        """Show **Filters - …** for predicates applied to the active surface data."""

        self._paint_applied_filters_labels(self._format_applied_filters_line(loading=loading))

    def _schedule_live_filter_refresh(self) -> None:
        """Debounce server-side grid refresh while the user types in column filters."""

        self._live_column_filter_timer.stop()  # type: ignore[attr-defined]
        self._live_column_filter_timer.start()  # type: ignore[attr-defined]

    def _apply_live_column_filter_query(self) -> None:
        """Reset to page 1 and reload the grid with current live column predicates."""

        self._page_index = 0
        self._refresh_surface_async()

    def _rebuild_column_filter_row(self, columns: list[str]) -> None:
        """Create one compact ``QLineEdit`` per visible column above the header row."""

        prev: dict[str, str] = {}
        if self._last_grid_columns:
            for j, name in enumerate(self._last_grid_columns):
                if j < len(self._column_filter_inputs):
                    prev[name] = self._column_filter_inputs[j].text()

        while self._filter_layout.count() > 1:
            item = self._filter_layout.takeAt(1)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._column_filter_inputs = []

        for col in columns:
            inp = QLineEdit()
            meta = self._ctx.meta.fields_by_name.get(col)
            if meta is not None and field_formats_as_measure(meta):
                inp.setPlaceholderText("Text or min-max")
            else:
                inp.setPlaceholderText("Filter")
            inp.setStyleSheet(
                "QLineEdit { padding: 2px 4px; font-size: 9px; background: #FFFFFF; "
                "border: 1px solid #BBD7F0; border-radius: 3px; }"
            )
            inp.setFixedHeight(self._FILTER_STRIP_ROW_HEIGHT - 2)
            if hasattr(inp, "setClearButtonEnabled"):
                try:
                    inp.setClearButtonEnabled(True)  # type: ignore[attr-defined]
                except Exception:
                    pass
            txt = prev.get(col, "")
            if self._session_header_filter_texts and col in self._session_header_filter_texts:
                txt = self._session_header_filter_texts[col]
            inp.setText(txt)
            inp.textChanged.connect(self._schedule_live_filter_refresh)  # type: ignore[attr-defined]
            self._filter_layout.addWidget(inp)
            self._column_filter_inputs.append(inp)
        if self._session_header_filter_texts:
            self._session_header_filter_texts.clear()

    def _sync_column_filter_geometry(self) -> None:
        """Match each filter box width to its table column and align with the vertical header."""

        if self._ui_surface != "grid":
            return

        row_h = self._FILTER_STRIP_ROW_HEIGHT
        self._filter_inner.setFixedHeight(row_h)

        if not self._column_filter_inputs:
            self._filter_corner.setFixedSize(
                max(self._grid_table.verticalHeader().width(), 1),
                row_h,
            )
            inner_w = self._filter_corner.width() + 1
            self._filter_inner.setFixedWidth(max(inner_w, 1))
            return

        vh = self._grid_table.verticalHeader()
        hdr = self._grid_table.horizontalHeader()
        corner_w = max(vh.width(), 1)
        self._filter_corner.setFixedSize(corner_w, row_h)

        for i, inp in enumerate(self._column_filter_inputs):
            if i >= hdr.count():
                break
            w = max(hdr.sectionSize(i), 24)
            inp.setFixedWidth(w)

        inner_w = corner_w + sum(max(hdr.sectionSize(i), 24) for i in range(len(self._column_filter_inputs)))
        self._filter_inner.setFixedWidth(max(inner_w, corner_w + 1))

        tb_bar = self._grid_table.horizontalScrollBar()
        fs_bar = self._filter_scroll.horizontalScrollBar()
        fs_bar.setMinimum(tb_bar.minimum())
        fs_bar.setMaximum(tb_bar.maximum())
        fs_bar.setSingleStep(tb_bar.singleStep())
        fs_bar.setPageStep(tb_bar.pageStep())
        fs_bar.setValue(tb_bar.value())

    def _wire_column_filter_header_signals(self) -> None:
        """Connect header resize / scroll signals once for filter strip alignment."""

        if self._ui_surface != "grid":
            return
        if self._column_filter_header_wired:
            return
        hdr = self._grid_table.horizontalHeader()
        hdr.sectionResized.connect(lambda *_a: self._sync_column_filter_geometry())  # type: ignore[attr-defined]
        hdr.geometriesChanged.connect(lambda *_a: self._sync_column_filter_geometry())  # type: ignore[attr-defined]
        vh = self._grid_table.verticalHeader()
        vh.geometriesChanged.connect(lambda *_a: self._sync_column_filter_geometry())  # type: ignore[attr-defined]
        self._grid_table.horizontalScrollBar().valueChanged.connect(  # type: ignore[attr-defined]
            self._filter_scroll.horizontalScrollBar().setValue
        )
        self._filter_scroll.horizontalScrollBar().valueChanged.connect(  # type: ignore[attr-defined]
            self._grid_table.horizontalScrollBar().setValue
        )
        self._column_filter_header_wired = True

    def _order_by_sql(self) -> str:
        """Return the comma-separated ``ORDER BY`` body (quoted identifiers).

        User sort keys run first; any primary-key column not already listed is
        appended ``ASC`` for stable pagination (default matches PK-only order).
        """

        return _build_grid_order_by_sql(self._grid_sort_specs, self._ctx.meta.file_key_columns)

    def _apply_grid_header_labels_with_sort(self, columns: list[str]) -> None:
        """Paint header labels with ↑/↓ sort markers and store raw names in ``UserRole``.

        Purpose
        -------
        Show which columns drive ``ORDER BY`` (including multi-sort priority via
        superscript ²…⁹ on secondary keys). ``Qt.UserRole`` keeps the bare field
        name for clipboard/export headers without arrows.

        Internal Logic
        ---------------
        For each visible column, if it appears in :attr:`_grid_sort_specs`, build
        ``NAME ↑`` or ``POP ↑²`` using Unicode arrows and superscripts; otherwise
        use uppercase name only. Assign ``QTableWidgetItem`` per logical section.

        Example invocation
        --------------------
        Called from :meth:`_render_grid` after row data is written.
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
        for i, col in enumerate(columns):
            base = col.upper()
            label = base
            for pos, (name, asc) in enumerate(self._grid_sort_specs):
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
            self._grid_table.setHorizontalHeaderItem(i, item)

    def _on_grid_header_sort_clicked(self, logical_index: int) -> None:
        """Apply header-click sorting (multi-sort with Ctrl; Shift resets to PK order).

        Purpose
        -------
        Let users reorder the DuckDB-backed grid: plain click sets or toggles a
        single-column sort; Ctrl (or macOS Cmd) adds or flips a column in a
        multi-key order; Shift clears user keys so only primary-key ordering runs.

        Internal Logic
        ---------------
        Resolve the field name from :attr:`_last_grid_columns`. Shift → clear
        :attr:`_grid_sort_specs`. Ctrl → toggle direction if the column is
        already listed, else append ``(col, True)``. Plain click → if the only
        key is this column, flip direction; else replace with ``[(col, True)]``.
        Reset to page 0 and refresh.

        Example invocation
        --------------------
        Connected once to ``QHeaderView.sectionClicked`` from :meth:`__init__``.
        """

        if self._ui_surface == "pivot":
            return
        if self._last_grid_columns is None:
            return
        if logical_index < 0 or logical_index >= len(self._last_grid_columns):
            return
        col = self._last_grid_columns[logical_index]

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
            self._grid_sort_specs = []
            self._page_index = 0
            self._refresh_surface_async()
            return

        specs = list(self._grid_sort_specs)
        if m_int & ctrl:
            found = -1
            for i, (name, _asc) in enumerate(specs):
                if name == col:
                    found = i
                    break
            if found >= 0:
                n, a = specs[found]
                specs[found] = (n, not a)
            else:
                specs.append((col, True))
            self._grid_sort_specs = specs
        else:
            if len(specs) == 1 and specs[0][0] == col:
                n, a = specs[0]
                self._grid_sort_specs = [(n, not a)]
            else:
                self._grid_sort_specs = [(col, True)]

        self._page_index = 0
        self._refresh_surface_async()

    def _visible_columns_in_current_order(self) -> list[str]:
        """Return visible columns in either meta order or alphabetical order."""

        meta_order = [f.name for f in self._ctx.meta.fields]
        visible = [c for c in meta_order if c in self._visible_columns_set]
        if self._sort_columns_by_name:
            return sorted(visible, key=lambda s: s.lower())
        return visible

    def _refresh_grid_async(self) -> None:
        """Refresh the grid table in a background thread."""

        visible_columns = self._visible_columns_in_current_order()
        if not visible_columns:
            # Always show at least PKs to keep the UI usable.
            visible_columns = [c for c in self._ctx.meta.file_key_columns if c in self._visible_columns_set]

        where_sql = self._combined_where_sql_for_query()
        order_by_sql = self._order_by_sql()
        limit = self._page_size
        offset = self._page_index * self._page_size

        self._update_pager_context_label(loading=True)

        # Keep references to avoid garbage collection while worker runs.
        if self._grid_thread is not None and self._grid_thread.isRunning():
            self._grid_thread.wait()
        self._grid_thread = None
        self._grid_worker = None

        thread = QThread()
        worker = _GridQueryWorker(
            database_path=self._ctx.database_path,
            table_name=self._ctx.table_name,
            where_sql=where_sql,
            order_by_sql=order_by_sql,
            visible_columns=visible_columns,
            limit=limit,
            offset=offset,
            host_thread=thread,
        )
        self._grid_thread = thread
        self._grid_worker = worker
        worker.moveToThread(thread)

        def on_finished(result: object) -> None:
            thread.wait()
            self._grid_thread = None
            self._grid_worker = None
            assert isinstance(result, dict)
            rows = result.get("rows", [])
            columns = result.get("columns", [])
            total = int(result.get("total", 0))

            self._total_rows = total
            self._render_grid(columns=list(columns), rows=list(rows))

        def on_failed(msg: str) -> None:
            thread.wait()
            self._grid_thread = None
            self._grid_worker = None
            QMessageBox.critical(self, "Grid refresh failed", msg)

        worker.finished.connect(on_finished)  # type: ignore[attr-defined]
        worker.failed.connect(on_failed)  # type: ignore[attr-defined]
        thread.started.connect(worker.run)  # type: ignore[attr-defined]
        thread.start()

        # Store for page label later.
        self._page_label.setText(
            f"Loading... page {self._page_index + 1}"
        )

    # Column index of the pivot tree expand/collapse affordance (leading column).
    _PIVOT_TREE_COL: int = 0

    def _pivot_row_group_end_exclusive(self, p: int) -> int:
        """Return the first row index after ``p`` that closes the subtree rooted at ``p``.

        Purpose
        -------
        When a subtotal at ``p`` is collapsed, every row with index in ``(p, end)``
        should be hidden until a row at the same or shallower depth appears.

        Internal Logic
        ---------------
        Read :attr:`_pivot_row_depths` for ``depths[p]`` and scan forward; the first
        row ``j > p`` with ``depths[j] <= depths[p]`` ends the open group. If none
        exists before the table end, return ``rowCount``.

        Example invocation
        --------------------
        ``end = self._pivot_row_group_end_exclusive(3)`` — ``end`` is exclusive upper
        bound for rows controlled by a collapsed node at row ``3``.
        """

        depths = self._pivot_row_depths
        n = len(depths)
        if p < 0 or p >= n:
            return n
        d_p = depths[p]
        for j in range(p + 1, n):
            if depths[j] <= d_p:
                return j
        return n

    def _pivot_should_hide_row(self, row: int) -> bool:
        """Return True when ``row`` sits under a collapsed pivot subtotal ancestor.

        Purpose
        -------
        Drive :meth:`QTableWidget.setRowHidden` so collapsed branches disappear
        without rebuilding the DuckDB pivot payload.

        Internal Logic
        ---------------
        Grand rows are never hidden. For each collapsed index ``p`` with kind
        ``subtotal``, if ``p < row < group_end(p)`` then ``row`` is a descendant
        and must be hidden.

        Example invocation
        --------------------
        ``hide = self._pivot_should_hide_row(12)`` after the user collapses row ``4``.
        """

        kinds = self._last_pivot_row_kinds
        if row < 0 or row >= len(kinds):
            return False
        if kinds[row] == "grand":
            return False
        for p in self._pivot_collapsed_rows:
            if p >= row:
                continue
            if p < 0 or p >= len(kinds) or kinds[p] != "subtotal":
                continue
            end = self._pivot_row_group_end_exclusive(p)
            if p < row < end:
                return True
        return False

    def _apply_pivot_tree_visibility(self) -> None:
        """Apply ``setRowHidden`` for every pivot row from collapse state.

        Purpose
        -------
        Keep the flat pivot model in the table while visually folding subtrees.

        Internal Logic
        ---------------
        Iterate each table row index and set hidden from :meth:`_pivot_should_hide_row`.

        Example invocation
        --------------------
        Called after toggling :attr:`_pivot_collapsed_rows` or **Expand/Collapse All**.
        """

        if self._ui_surface != "pivot":
            return
        for r in range(self._grid_table.rowCount()):
            self._grid_table.setRowHidden(r, self._pivot_should_hide_row(r))

    def _pivot_tree_glyph_for_row(self, r: int) -> str:
        """Build the leading-column text (indent + ``+`` / ``-``) for row ``r``.

        Purpose
        -------
        Show hierarchy affordance only on expandable subtotal rows.

        Internal Logic
        ---------------
        Grand and non-expandable rows return empty. Otherwise prefix two spaces per
        depth unit, then ``+`` when collapsed else ``-``.

        Example invocation
        --------------------
        ``text = self._pivot_tree_glyph_for_row(0)`` may return ``"-"`` for an expanded root.
        """

        kinds = self._last_pivot_row_kinds
        if r < 0 or r >= len(kinds):
            return ""
        if kinds[r] == "grand":
            return ""
        if r >= len(self._pivot_expandable_flags) or not self._pivot_expandable_flags[r]:
            return ""
        depth = self._pivot_row_depths[r] if r < len(self._pivot_row_depths) else 0
        indent = "  " * max(0, int(depth))
        if r in self._pivot_collapsed_rows:
            return f"{indent}+"
        return f"{indent}-"

    def _refresh_pivot_expand_column_glyphs(self) -> None:
        """Rewrite column ``_PIVOT_TREE_COL`` items to match collapse state.

        Purpose
        -------
        Keep ``+`` / ``-`` glyphs in sync after refresh, expand all, or row toggles.

        Internal Logic
        ---------------
        For each row, set column ``0`` text from :meth:`_pivot_tree_glyph_for_row`
        and center-align when non-empty.

        Example invocation
        --------------------
        ``self._refresh_pivot_expand_column_glyphs()`` after :meth:`_render_grid` fills cells.
        """

        if self._ui_surface != "pivot":
            return
        c = self._PIVOT_TREE_COL
        for r in range(self._grid_table.rowCount()):
            it = self._grid_table.item(r, c)
            if it is None:
                continue
            glyph = self._pivot_tree_glyph_for_row(r)
            it.setText(glyph)
            try:
                align = int(Qt.AlignCenter | Qt.AlignVCenter) if glyph else int(Qt.AlignLeft | Qt.AlignVCenter)  # type: ignore[attr-defined]
                it.setTextAlignment(align)
            except Exception:
                pass

    def _on_pivot_tree_cell_clicked(self, row: int, col: int) -> None:
        """Toggle collapse when the user clicks the tree column on an expandable row.

        Purpose
        -------
        Wire ``QTableWidget.cellClicked`` for pivot-only expand/collapse.

        Internal Logic
        ---------------
        Ignore non-pivot surfaces and non-tree columns. Require
        :attr:`_pivot_expandable_flags` at ``row``; flip membership in
        :attr:`_pivot_collapsed_rows`, then refresh visibility and glyphs.

        Example invocation
        --------------------
        ``self._grid_table.cellClicked.connect(self._on_pivot_tree_cell_clicked)``
        """

        if self._ui_surface != "pivot" or col != self._PIVOT_TREE_COL:
            return
        if row < 0 or row >= len(self._pivot_expandable_flags):
            return
        if not self._pivot_expandable_flags[row]:
            return
        if row in self._pivot_collapsed_rows:
            self._pivot_collapsed_rows.discard(row)
        else:
            self._pivot_collapsed_rows.add(row)
        self._apply_pivot_tree_visibility()
        self._refresh_pivot_expand_column_glyphs()

    def _on_pivot_expand_all_clicked(self) -> None:
        """Clear all collapsed pivot nodes and show every row.

        Purpose
        -------
        **Expand All** toolbar control for the pivot grid.

        Internal Logic
        ---------------
        ``clear`` :attr:`_pivot_collapsed_rows`, then re-run visibility and glyph refresh.

        Example invocation
        --------------------
        ``self._pivot_expand_all_btn.clicked.connect(self._on_pivot_expand_all_clicked)``
        """

        if self._ui_surface != "pivot":
            return
        self._pivot_collapsed_rows.clear()
        self._apply_pivot_tree_visibility()
        self._refresh_pivot_expand_column_glyphs()

    def _on_pivot_collapse_all_clicked(self) -> None:
        """Collapse every expandable subtotal row in the current pivot.

        Purpose
        -------
        **Collapse All** hides nested detail/subtotal rows under each branch head.

        Internal Logic
        ---------------
        Set :attr:`_pivot_collapsed_rows` to all indices where
        :attr:`_pivot_expandable_flags` is true, then refresh visibility and glyphs.

        Example invocation
        --------------------
        ``self._pivot_collapse_all_btn.clicked.connect(self._on_pivot_collapse_all_clicked)``
        """

        if self._ui_surface != "pivot":
            return
        self._pivot_collapsed_rows = {
            i for i, ex in enumerate(self._pivot_expandable_flags) if ex
        }
        self._apply_pivot_tree_visibility()
        self._refresh_pivot_expand_column_glyphs()

    def _render_grid(self, columns: list[str], rows: list[Tuple[object, ...]]) -> None:
        """Render grid table data with formatting and pagination metadata."""

        try:
            self._grid_table.clearContents()
        except Exception:
            self._grid_table.clear()
        self._grid_table.setRowCount(len(rows))

        is_pivot = self._ui_surface == "pivot"
        tree_c = self._PIVOT_TREE_COL
        if is_pivot:
            self._grid_table.setColumnCount(1 + len(columns))
            disp_columns = [""] + list(columns)
            saved_specs = self._grid_sort_specs
            self._grid_sort_specs = []
            self._apply_grid_header_labels_with_sort(disp_columns)
            self._grid_sort_specs = saved_specs
        else:
            self._grid_table.setColumnCount(len(columns))
            self._apply_grid_header_labels_with_sort(columns)

        n_rd = len(self._last_pivot_row_dims)
        kinds = self._last_pivot_row_kinds
        if len(kinds) != len(rows):
            kinds = ["detail"] * len(rows)

        bold_font = QFont()
        try:
            bold_font.setBold(True)
        except Exception:
            pass

        for r_idx, row in enumerate(rows):
            rk = kinds[r_idx] if r_idx < len(kinds) else "detail"
            if is_pivot:
                tree_item = QTableWidgetItem("")
                try:
                    tree_item.setTextAlignment(int(Qt.AlignCenter | Qt.AlignVCenter))
                except Exception:
                    pass
                if rk in ("subtotal", "grand"):
                    try:
                        tree_item.setFont(bold_font)
                    except Exception:
                        pass
                if rk == "grand":
                    try:
                        tree_item.setBackground(QColor("#D7ECFF"))
                    except Exception:
                        pass
                elif rk == "detail" and r_idx % 2 == 1:
                    tree_item.setBackground(QColor("#EDF6FF"))
                self._grid_table.setItem(r_idx, tree_c, tree_item)
            for c_idx, value in enumerate(row):
                col_name = columns[c_idx]
                data_col = (tree_c + 1 + c_idx) if is_pivot else c_idx
                field_meta = self._ctx.meta.fields_by_name.get(col_name)
                if is_pivot:
                    use_measure_fmt = c_idx >= n_rd
                else:
                    use_measure_fmt = field_meta is not None and field_formats_as_measure(field_meta)
                if use_measure_fmt:
                    text = _format_measure_cell(value, self._ctx.measure_decimal_places)
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
                else:
                    text = _format_dimension_cell(value, field_meta)
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(int(Qt.AlignLeft | Qt.AlignVCenter))

                if is_pivot:
                    if rk in ("subtotal", "grand"):
                        try:
                            item.setFont(bold_font)
                        except Exception:
                            pass
                    if rk == "grand":
                        try:
                            item.setBackground(QColor("#D7ECFF"))
                        except Exception:
                            pass
                    elif rk == "detail" and r_idx % 2 == 1:
                        item.setBackground(QColor("#EDF6FF"))
                else:
                    if r_idx % 2 == 1:
                        item.setBackground(QColor("#EDF6FF"))
                self._grid_table.setItem(r_idx, data_col, item)

        if is_pivot:
            self._refresh_pivot_expand_column_glyphs()
            self._apply_pivot_tree_visibility()

        if is_pivot:
            self._page_label.setText(f"Pivot rows: {len(rows)}")
        else:
            pages = max(1, math.ceil(self._total_rows / self._page_size)) if self._page_size else 1
            self._page_label.setText(f"Page {self._page_index + 1} / {pages} (Rows: {self._total_rows})")

        # Minimize scrolling: resize columns to contents with caps.
        try:
            self._grid_table.resizeColumnsToContents()
            for c in range(self._grid_table.columnCount()):
                current = self._grid_table.columnWidth(c)
                if is_pivot and c == tree_c:
                    self._grid_table.setColumnWidth(c, min(max(current, 44), 160))
                else:
                    self._grid_table.setColumnWidth(c, min(max(current, 70), 220))
        except Exception:
            pass

        prev_key_before = tuple(self._last_grid_columns) if self._last_grid_columns is not None else None
        cols_key = tuple(columns)
        self._last_grid_columns = list(columns)
        if self._ui_surface == "grid":
            if cols_key != prev_key_before:
                self._rebuild_column_filter_row(columns)
            self._sync_column_filter_geometry()
            self._wire_column_filter_header_signals()
            try:
                QTimer.singleShot(0, self._sync_column_filter_geometry)  # type: ignore[attr-defined]
            except Exception:
                pass

        self._update_pager_context_label(loading=False)

    def _on_prev_page(self) -> None:
        """Navigate to the previous page."""

        if self._ui_surface == "pivot":
            return
        if self._page_index > 0:
            self._page_index -= 1
            self._refresh_surface_async()

    def _on_next_page(self) -> None:
        """Navigate to the next page."""

        if self._ui_surface == "pivot":
            return
        max_page = max(0, math.ceil(self._total_rows / self._page_size) - 1) if self._page_size else 0
        if self._page_index < max_page:
            self._page_index += 1
            self._refresh_surface_async()

    def _rebuild_drawer_list(self) -> None:
        """Populate the hamburger drawer list with checkboxes."""

        self._drawer_updating = True
        self._drawer_list.clear()

        all_columns = [f.name for f in self._ctx.meta.fields]
        sorted_columns = sorted(all_columns, key=lambda s: s.lower())

        # Ensure primary-key columns stay visible (drawer cannot uncheck them).
        for pk in self._ctx.meta.file_key_columns:
            if pk in all_columns:
                self._visible_columns_set.add(pk)

        for col in sorted_columns:
            item = QListWidgetItem(col)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            state = Qt.Checked if col in self._visible_columns_set else Qt.Unchecked
            item.setCheckState(state)
            self._drawer_list.addItem(item)
        self._drawer_updating = False

    def _on_drawer_item_changed(self, item: QListWidgetItem) -> None:
        """Update visible column set when a drawer checkbox changes."""

        if getattr(self, "_drawer_updating", False):
            return

        try:
            col = item.text()
        except Exception:
            return

        # Primary keys must stay visible.
        if col in self._ctx.meta.file_key_columns:
            item.setCheckState(Qt.Checked)
            self._visible_columns_set.add(col)
            return

        if item.checkState() == Qt.Checked:
            self._visible_columns_set.add(col)
        else:
            self._visible_columns_set.discard(col)

        self._page_index = 0
        self._refresh_surface_async()

    def _refresh_drawer_visibility(self) -> None:
        """Hide drawer rows that do not match the search query."""

        q = self._drawer_search.text().strip().lower()
        for i in range(self._drawer_list.count()):
            it = self._drawer_list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _on_drawer_select_all(self) -> None:
        """Select all columns in drawer when Select All is checked."""

        if self._drawer_select_all.isChecked():
            for i in range(self._drawer_list.count()):
                it = self._drawer_list.item(i)
                it.setCheckState(Qt.Checked)
            self._visible_columns_set = {self._drawer_list.item(i).text() for i in range(self._drawer_list.count())}
            self._refresh_surface_async()
        else:
            # If unchecked, do nothing (Deselect All handles clearing).
            pass

    def _on_drawer_deselect_all(self) -> None:
        """Deselect all columns in drawer when Deselect All is checked."""

        if self._drawer_deselect_all.isChecked():
            self._visible_columns_set = set(self._ctx.meta.file_key_columns)
            for i in range(self._drawer_list.count()):
                it = self._drawer_list.item(i)
                it.setCheckState(Qt.Checked if it.text() in self._visible_columns_set else Qt.Unchecked)
            self._refresh_surface_async()

    def _on_copy_excel(self) -> None:
        """Copy the current grid or pivot view to the clipboard as TSV (Excel-friendly)."""

        cols_out: list[str] = []
        for c in range(self._grid_table.columnCount()):
            it = self._grid_table.horizontalHeaderItem(c)
            if it is not None:
                raw = it.data(Qt.UserRole)  # type: ignore[attr-defined]
                if raw is not None and str(raw).strip() != "":
                    cols_out.append(str(raw).upper())
                else:
                    cols_out.append(it.text())
            else:
                cols_out.append("")

        visible_rows = []
        for r in range(self._grid_table.rowCount()):
            if self._grid_table.isRowHidden(r):
                continue
            row_vals = []
            for c in range(self._grid_table.columnCount()):
                item = self._grid_table.item(r, c)
                row_vals.append(item.text() if item is not None else "")
            visible_rows.append(row_vals)

        tsv_lines = ["\t".join(cols_out)]
        for row_vals in visible_rows:
            tsv_lines.append("\t".join(row_vals))
        tsv_text = "\n".join(tsv_lines)

        try:
            clipboard = QApplication.clipboard()
            clipboard.setText(tsv_text, QClipboard.Clipboard)
        except Exception:
            QMessageBox.critical(self, "Clipboard failed", "Failed to copy to clipboard.")

    def _on_export_filtered_csv(self) -> None:
        """Export the filtered result set to CSV (user-chosen path).

        In pivot mode, exports the last successful pivot query as a single CSV.
        """

        if self._ui_surface == "pivot":
            suggested_name = "pivot_output.csv"
            caption = "Export Pivot CSV"
            if self._grid_table.rowCount() == 0:
                QMessageBox.warning(
                    self,
                    "Nothing to export",
                    "Run **Update Pivot** first so the pivot table has rows to export.",
                )
                return
        else:
            suggested_name = "filtered_output.csv"
            caption = "Export Filtered CSV"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            caption,
            suggested_name,
            "CSV files (*.csv);;All files (*)",
        )
        if not save_path:
            return

        out_path = Path(save_path).resolve()
        out_sql_lit = str(out_path).replace("\\", "/").replace("'", "''")

        if self._ui_surface == "pivot":
            try:
                import csv

                with out_path.open("w", newline="", encoding="utf-8") as fp:
                    writer = csv.writer(fp)
                    headers: list[str] = []
                    for c in range(self._grid_table.columnCount()):
                        hit = self._grid_table.horizontalHeaderItem(c)
                        if hit is not None:
                            raw = hit.data(Qt.UserRole)  # type: ignore[attr-defined]
                            if raw is not None and str(raw).strip() != "":
                                headers.append(str(raw))
                            else:
                                headers.append(hit.text())
                        else:
                            headers.append("")
                    writer.writerow(headers)
                    for r in range(self._grid_table.rowCount()):
                        if self._grid_table.isRowHidden(r):
                            continue
                        row_out: list[str] = []
                        for c in range(self._grid_table.columnCount()):
                            cell = self._grid_table.item(r, c)
                            row_out.append(cell.text() if cell is not None else "")
                        writer.writerow(row_out)
                QMessageBox.information(self, "Export complete", f"Saved to:\n{out_path}")
            except Exception as e:
                QMessageBox.critical(self, "Export failed", str(e))
            return

        where_sql = self._combined_where_sql_for_query()
        visible_cols = self._visible_columns_in_current_order()

        # Export in a background thread to avoid blocking.
        class _ExportWorker(QObject):
            finished = pyqtSignal(str)
            failed = pyqtSignal(str)

            def __init__(self_nonlocal, host_thread: QThread) -> None:
                super().__init__()
                self_nonlocal._host_thread = host_thread

            def run(self_nonlocal) -> None:
                """Run DuckDB COPY into the user's chosen path."""

                try:
                    import duckdb  # type: ignore

                    conn = duckdb.connect(database=str(self._ctx.database_path))
                    try:
                        where_clause = f"WHERE {where_sql}" if where_sql.strip() else ""
                        cols_sql = ", ".join(visible_cols)
                        order_sql = self._order_by_sql()
                        sql = f"""
                            COPY (
                                SELECT {cols_sql}
                                FROM {self._ctx.table_name}
                                {where_clause}
                                ORDER BY {order_sql}
                            ) TO '{out_sql_lit}' (HEADER, DELIMITER ',');
                        """
                        conn.execute(sql)
                    finally:
                        conn.close()
                    self_nonlocal.finished.emit(str(out_path))
                except Exception as e:
                    self_nonlocal.failed.emit(str(e))
                finally:
                    self_nonlocal._host_thread.quit()

        if self._export_thread is not None and self._export_thread.isRunning():
            self._export_thread.wait()
        self._export_thread = None
        self._export_worker = None

        thread = QThread()
        worker = _ExportWorker(thread)
        self._export_thread = thread
        self._export_worker = worker
        worker.moveToThread(thread)

        def on_finished(path: str) -> None:
            thread.wait()
            self._export_thread = None
            self._export_worker = None
            QMessageBox.information(self, "Export complete", f"Saved to:\n{path}")

        def on_failed(msg: str) -> None:
            thread.wait()
            self._export_thread = None
            self._export_worker = None
            QMessageBox.critical(self, "Export failed", msg)

        worker.finished.connect(on_finished)  # type: ignore[attr-defined]
        worker.failed.connect(on_failed)  # type: ignore[attr-defined]
        thread.started.connect(worker.run)  # type: ignore[attr-defined]
        thread.start()


__all__ = ["DataGridTab"]

