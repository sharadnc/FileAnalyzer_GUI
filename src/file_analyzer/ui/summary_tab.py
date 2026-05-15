"""Summary tab: per-field profiling, quality metrics, and duplicate detection."""

from __future__ import annotations

from typing import Optional

from file_analyzer.automated_profiling_report import build_automated_profiling_report_html
from file_analyzer.summary_clipboard_html import build_summary_clipboard_html_document
from file_analyzer.summary_reports import (
    DatasetSummaryReport,
    FieldSummaryReport,
    compute_dataset_summary_report,
    format_summary_plaintext,
)
from file_analyzer.ui.models import LoadedDatasetContext

try:
    from PyQt5.QtCore import QObject, QMimeData, Qt, QThread, pyqtSignal
    from PyQt5.QtGui import QFont, QTextDocument
    from PyQt5.QtWidgets import (
        QApplication,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QLabel,
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
    QObject = object  # type: ignore[assignment]
    QMimeData = object  # type: ignore[assignment]
    Qt = object  # type: ignore[assignment]
    QThread = object  # type: ignore[assignment]
    pyqtSignal = object  # type: ignore[assignment]
    QFont = object  # type: ignore[assignment]
    QTextDocument = object  # type: ignore[assignment]
    QApplication = object  # type: ignore[assignment]
    QFrame = object  # type: ignore[assignment]
    QGroupBox = object  # type: ignore[assignment]
    QHBoxLayout = object  # type: ignore[assignment]
    QLabel = object  # type: ignore[assignment]
    QMessageBox = object  # type: ignore[assignment]
    QPushButton = object  # type: ignore[assignment]
    QScrollArea = object  # type: ignore[assignment]
    QSizePolicy = object  # type: ignore[assignment]
    QSplitter = object  # type: ignore[assignment]
    QTableWidget = object  # type: ignore[assignment]
    QTableWidgetItem = object  # type: ignore[assignment]
    QVBoxLayout = object  # type: ignore[assignment]
    QWidget = object  # type: ignore[assignment]


class _SummaryComputeWorker(QObject):
    """Background DuckDB pass for :func:`compute_dataset_summary_report`."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, ctx: LoadedDatasetContext, host_thread: QThread) -> None:
        """Store dataset context and owning ``QThread`` for :meth:`run`."""

        super().__init__()
        self._ctx = ctx
        self._host_thread = host_thread

    def run(self) -> None:
        """Compute the report and emit ``finished`` or ``failed``; then stop the thread."""

        try:
            rep = compute_dataset_summary_report(
                database_path=str(self._ctx.database_path),
                table_name=self._ctx.table_name,
                meta=self._ctx.meta,
            )
            self.finished.emit(rep)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self._host_thread.quit()


def _span_bold_yellow(display: str, highlight: bool) -> str:
    """Return *display* or the same text wrapped for rich-text emphasis.

    Purpose
    -------
    On the Summary tab, draw attention to quality metrics that are strictly above
    zero (nulls, blank strings, missing density, IQR outlier counts) using bold
    type and a pale yellow background inside a :class:`QLabel` HTML fragment.

    Internal Logic
    ----------------
    If *highlight* is false, return *display* unchanged. Otherwise return a
    single ``span`` element with inline CSS (``font-weight: bold``,
    ``background-color``) suitable for concatenation into a larger HTML string.

    Example invocation
    ------------------
    >>> _span_bold_yellow("0", False)
    '0'
    >>> "<span" in _span_bold_yellow("12", True)
    True
    """

    if not highlight:
        return display
    # Pale yellow reads clearly on the light blue group background.
    return (
        '<span style="font-weight: bold; background-color: #fff59d; padding: 0 2px; '
        f'border-radius: 2px;">{display}</span>'
    )


def _fill_table(table: QTableWidget, headers: list[str], rows: list[list[str]]) -> None:
    """Set headers and row text on a read-only ``QTableWidget``.

    Purpose
    -------
    Populate histogram / top-value grids and emphasize header labels (column
    names) in bold for report-style readability.

    Internal Logic
    ----------------
    Clear the widget, assign header strings, apply a bold :class:`QFont` to
    :meth:`QHeaderView.setFont` on the horizontal header, then fill body cells
    and bold the special ``Total`` row when the first cell matches that label.
    """

    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    bold_header = QFont()
    bold_header.setBold(True)
    table.horizontalHeader().setFont(bold_header)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            item = QTableWidgetItem(cell)
            if row and str(row[0]).strip().lower() == "total":
                bf = QFont()
                bf.setBold(True)
                item.setFont(bf)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()


def _field_section(fr: FieldSummaryReport) -> QGroupBox:
    """Build one group for a single :class:`FieldSummaryReport` with a darker frame."""

    title = f"{fr.field_name.upper()}  ({fr.field_dtype} · inferred: {fr.inferred_semantic})"
    box = QGroupBox(title)
    try:
        box.setStyleSheet(
            """
            QGroupBox {
                font-weight: bold;
                border: 2px solid #3b7cb8;
                border-radius: 6px;
                margin-top: 14px;
                padding-top: 10px;
                background-color: #f2f8fd;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #000000;
            }
            """
        )
    except Exception:
        pass

    outer = QVBoxLayout(box)

    type_line = (
        f"Meta type: {fr.meta_field_type} · DuckDB typeof(sample): {fr.duckdb_typeof_sample} · "
        f"High cardinality: {'yes' if fr.is_high_cardinality else 'no'}"
    )
    lbl_types = QLabel(type_line)
    lbl_types.setWordWrap(True)
    try:
        lbl_types.setStyleSheet("font-weight: normal;")
    except Exception:
        pass
    outer.addWidget(lbl_types)

    null_disp = _span_bold_yellow(str(fr.null_count), fr.null_count > 0)
    empty_disp = _span_bold_yellow(str(fr.empty_string_count), fr.empty_string_count > 0)
    miss_pct_disp = _span_bold_yellow(f"{fr.missing_pct:.2f}%", fr.missing_pct > 0.0)
    miss = (
        f"Rows: {fr.row_count} · Nulls: {null_disp} · Empty/blank strings: {empty_disp} · "
        f"Missing density: {miss_pct_disp} · Distinct: {fr.distinct_count} · "
        f"Unique % of rows: {fr.unique_pct:.2f}%"
    )
    lbl_miss = QLabel(miss)
    lbl_miss.setWordWrap(True)
    try:
        lbl_miss.setTextFormat(Qt.RichText)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        lbl_miss.setStyleSheet("font-weight: normal;")
    except Exception:
        pass
    outer.addWidget(lbl_miss)

    if fr.numeric_mean is not None or fr.numeric_median is not None:
        stats = (
            f"Min: {fr.numeric_min} · Max: {fr.numeric_max} · Mean: {fr.numeric_mean} · "
            f"Median: {fr.numeric_median} · Std.dev (pop): {fr.numeric_stddev_pop} · "
            f"Variance (pop): {fr.numeric_variance_pop}"
        )
        sl = QLabel(stats)
        try:
            sl.setStyleSheet("font-weight: normal;")
        except Exception:
            pass
        outer.addWidget(sl)

    if fr.outlier_count_iqr is not None and fr.outlier_pct is not None:
        oc = fr.outlier_count_iqr
        out_disp = _span_bold_yellow(str(oc), oc > 0)
        ol = QLabel(
            f"IQR outliers (Tukey 1.5×IQR): {out_disp} rows "
            f"({fr.outlier_pct:.2f}% of non-null numeric values)"
        )
        try:
            ol.setTextFormat(Qt.RichText)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            ol.setStyleSheet("font-weight: normal;")
        except Exception:
            pass
        outer.addWidget(ol)

    if fr.histogram_bins:
        hl = QLabel(
            "Value ranges (equal-width numeric bins; up to 50 densest bins, then Others; Total = all rows):"
        )
        hl.setWordWrap(True)
        try:
            hl.setStyleSheet("font-weight: normal;")
        except Exception:
            pass
        outer.addWidget(hl)
        ht = QTableWidget()
        ht.setEditTriggers(QTableWidget.NoEditTriggers)
        ht.setSelectionMode(QTableWidget.NoSelection)
        _fill_table(ht, ["Bin range", "Count"], [[a, str(b)] for a, b in fr.histogram_bins])
        ht.setMaximumHeight(min(520, 28 + 22 * len(fr.histogram_bins)))
        outer.addWidget(ht)

    if fr.top_string_values:
        vl = QLabel(
            "Top values (string form; up to 50 values, then Others; Total = all rows):"
        )
        vl.setWordWrap(True)
        try:
            vl.setStyleSheet("font-weight: normal;")
        except Exception:
            pass
        outer.addWidget(vl)
        vt = QTableWidget()
        vt.setEditTriggers(QTableWidget.NoEditTriggers)
        vt.setSelectionMode(QTableWidget.NoSelection)
        _fill_table(vt, ["Value", "Count"], [[v, str(c)] for v, c in fr.top_string_values])
        vt.setMaximumHeight(min(520, 28 + 22 * len(fr.top_string_values)))
        outer.addWidget(vt)

    return box


def _automated_profiling_report_box(ctx: LoadedDatasetContext, rep: DatasetSummaryReport) -> QGroupBox:
    """Build the fixed-template executive profiling block at the top of the Summary tab.

    Purpose
    -------
    Host :func:`~file_analyzer.automated_profiling_report.build_automated_profiling_report_html`
    in a framed group so it reads as the first analytic artifact before per-field groups.

    Internal Logic
    ----------------
    Create a :class:`QGroupBox`, apply a light frame style, set a rich-text
    :class:`QLabel` from ``ctx`` + ``rep``, and return the group for the scroll layout.

    Example invocation
    ------------------
    ``layout.addWidget(_automated_profiling_report_box(self._ctx, rep))``
    """

    box = QGroupBox("Automated Data Profiling Report")
    try:
        box.setStyleSheet(
            """
            QGroupBox {
                font-weight: bold;
                border: 2px solid #2d6a4e;
                border-radius: 6px;
                margin-top: 14px;
                padding-top: 10px;
                background-color: #f4faf6;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #000000;
            }
            """
        )
    except Exception:
        pass
    inner = QVBoxLayout(box)
    prof = QLabel(build_automated_profiling_report_html(ctx, rep))
    prof.setWordWrap(True)
    try:
        prof.setTextFormat(Qt.RichText)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        prof.setStyleSheet("font-weight: normal;")
    except Exception:
        pass
    inner.addWidget(prof)
    return box


class SummaryTab(QWidget):
    """Scrollable per-field summary after **Load Data**."""

    def __init__(self, ctx: LoadedDatasetContext) -> None:
        """Start background summary computation and show a loading strip."""

        super().__init__()
        self._ctx = ctx
        self._thread: Optional[QThread] = None
        self._worker: Optional[_SummaryComputeWorker] = None
        self._last_report: Optional[DatasetSummaryReport] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        header_host = QWidget()
        header = QHBoxLayout(header_host)
        header.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("Building summary…")
        self._status.setWordWrap(True)
        header.addWidget(self._status, 1)
        self._copy_btn = QPushButton("Copy to Clipboard")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._on_copy_to_clipboard)  # type: ignore[attr-defined]
        header.addWidget(self._copy_btn, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._inner = QWidget()
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setSpacing(10)
        self._scroll.setWidget(self._inner)
        self._scroll.setFrameShape(QFrame.NoFrame)  # type: ignore[attr-defined]
        self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)  # type: ignore[attr-defined]

        self._layout_splitter = QSplitter(Qt.Vertical)  # type: ignore[attr-defined]
        self._layout_splitter.setChildrenCollapsible(False)
        self._layout_splitter.addWidget(header_host)
        self._layout_splitter.addWidget(self._scroll)
        self._layout_splitter.setStretchFactor(0, 0)
        self._layout_splitter.setStretchFactor(1, 1)
        self._layout_splitter.setMinimumHeight(160)
        self._layout_splitter.setObjectName("fa_layout_summary_header_body")
        root.addWidget(self._layout_splitter, 1)
        try:
            from PyQt5.QtCore import QSettings  # type: ignore[import-not-found]

            from file_analyzer.ui.layout_persistence import restore_splitter_state, wire_splitter_autosave

            _st = QSettings()
            restore_splitter_state(_st, self._layout_splitter, self._layout_splitter.objectName())
            wire_splitter_autosave(self._layout_splitter, self._layout_splitter.objectName(), self)
        except Exception:
            pass

        self._start_worker()

    def _start_worker(self) -> None:
        """Spawn a ``QThread`` running :class:`_SummaryComputeWorker`."""

        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = QThread()
        self._worker = _SummaryComputeWorker(self._ctx, self._thread)
        self._worker.moveToThread(self._thread)

        def on_done(rep: object) -> None:
            assert isinstance(rep, DatasetSummaryReport)
            thr = self._thread
            if thr is not None:
                thr.wait()
            self._thread = None
            self._worker = None
            self._last_report = rep
            self._populate(rep)
            self._status.setText(
                f"Summary ready — {len(rep.fields)} field(s); duplicate extra rows: "
                f"{rep.duplicates.duplicate_extra_rows} ({rep.duplicates.duplicate_pct:.2f}%)"
            )
            self._copy_btn.setEnabled(True)

        def on_err(msg: str) -> None:
            thr = self._thread
            if thr is not None:
                thr.wait()
            self._thread = None
            self._worker = None
            self._status.setText("Summary failed.")
            QMessageBox.critical(self, "Summary failed", msg)

        self._worker.finished.connect(on_done)  # type: ignore[attr-defined]
        self._worker.failed.connect(on_err)  # type: ignore[attr-defined]
        self._thread.started.connect(self._worker.run)  # type: ignore[attr-defined]
        self._thread.start()

    def _populate(self, rep: DatasetSummaryReport) -> None:
        """Clear the scroll area and rebuild all field sections."""

        while self._inner_layout.count():
            item = self._inner_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._inner_layout.addWidget(_automated_profiling_report_box(self._ctx, rep))

        dup = rep.duplicates
        banner = QLabel(
            f"<b>Dataset</b> — rows: {dup.total_rows}; distinct full rows: {dup.distinct_full_rows}; "
            f"<b>duplicate extra rows</b> (identical full-row copies): {dup.duplicate_extra_rows} "
            f"({dup.duplicate_pct:.2f}%)"
        )
        banner.setWordWrap(True)
        try:
            banner.setTextFormat(Qt.RichText)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._inner_layout.addWidget(banner)

        intro = QLabel(
            "Per field: dimensions (D) are profiled as <b>string</b> values (not coerced to numeric); "
            "measures (M) show descriptive stats and up to 50 densest numeric bin ranges plus Others and a "
            "<b>Total</b> row matching row count. Missingness, IQR outliers, and duplicate tracking apply as before."
        )
        intro.setWordWrap(True)
        try:
            intro.setTextFormat(Qt.RichText)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._inner_layout.addWidget(intro)

        for fr in rep.fields:
            self._inner_layout.addWidget(_field_section(fr))

        self._inner_layout.addStretch(1)

    def _on_copy_to_clipboard(self) -> None:
        """Copy the full Summary tab as HTML plus a plain-text fallback to the clipboard.

        Purpose
        -------
        Let users paste into Word, Outlook, or browsers with headings, tables,
        and highlights preserved, while plain-text editors still receive a
        readable linearization.

        Internal Logic
        ----------------
        Build HTML via :func:`~file_analyzer.summary_clipboard_html.build_summary_clipboard_html_document`,
        derive plain text with :class:`QTextDocument.toPlainText`, pack both into
        :class:`QMimeData`, and call :meth:`QClipboard.setMimeData`.
        """

        if self._last_report is None:
            return
        html_doc = build_summary_clipboard_html_document(self._ctx, self._last_report)
        try:
            doc = QTextDocument()
            doc.setHtml(html_doc)
            plain = doc.toPlainText()
        except Exception:
            plain = format_summary_plaintext(self._last_report)
        try:
            mime = QMimeData()
            mime.setHtml(html_doc)
            mime.setText(plain)
            QApplication.clipboard().setMimeData(mime)  # type: ignore[attr-defined]
        except Exception:
            try:
                QApplication.clipboard().setText(plain)  # type: ignore[attr-defined]
            except Exception:
                QMessageBox.warning(self, "Clipboard", "Could not copy to clipboard.")

    def wait_for_background_threads(self) -> None:
        """Join the summary worker thread before the tab is destroyed."""

        t = self._thread
        if t is not None and t.isRunning():
            t.wait()


__all__ = ["SummaryTab"]
