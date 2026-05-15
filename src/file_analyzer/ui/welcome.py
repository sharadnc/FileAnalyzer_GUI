"""Main window for File Analyzer (PyQt5).

This module implements:
- A persistent top strip (:meth:`QMainWindow.setMenuWidget`) with data path,
  optional **Browse meta** (no meta path text field; meta defaults from the data file name),
  **Browse Templates** (searchable list of ``templates/`` file stems),
  delimiter, **# of decimals**, browse buttons, and **Load Data**.
- A central :class:`QTabWidget` with **Summary**, **Visualize**, **Data Grid**, and
  **Pivot Data** tabs that
  fill with real content after a successful **Load Data** (background load worker).
- A status bar with a **Zoom** slider (bottom-right, Word/Excel style) scales UI text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from file_analyzer.config import load_app_config
from file_analyzer.duckdb_session import DuckDBSession, DuckDBSessionConfig
from file_analyzer.meta_parser import parse_meta_file, validate_meta_file_before_load
from file_analyzer.stats_service import apply_measure_decimal_rounding_to_quick_stats, compute_quick_stats_parallel
from file_analyzer.ui.models import LoadedDatasetContext


def _resolve_qss_path() -> Path:
    """Resolve the stylesheet QSS path."""

    return Path(__file__).resolve().parent / "styles.qss"


def derive_meta_path(data_path: Path) -> Path:
    """Derive the ``*_Meta`` file path from a data file path.

    Purpose
    -------
    The sample uses ``<filename>_Meta`` with the original extension preserved,
    e.g. ``NST-EST2025-ALLDATA.csv_Meta``.

    Internal Logic
    ---------------
    Append ``_Meta`` to the file name (not to the stem).

    Parameters
    ----------
    data_path:
        Path to the dataset file.

    Returns
    -------
    Path
        Path to the metadata file.
    """

    return data_path.with_name(data_path.name + "_Meta")


def _repo_root() -> Path:
    """Return the repository root directory (parent of ``src/``).

    Purpose
    -------
    Resolve bundled sample paths relative to the checkout so defaults work when
    the project is not located on the ``G:`` drive.

    Internal Logic
    ----------------
    ``welcome.py`` lives at ``src/file_analyzer/ui/welcome.py``; the repo root
    is three parents above that file.

    Example invocation
    ------------------
    >>> _repo_root().name  # doctest: +SKIP
    'AI_FileAnalyze'
    """

    return Path(__file__).resolve().parents[3]


def default_sample_data_path() -> Path:
    """Pick the default data file path (``.env`` override, then legacy fallbacks).

    Purpose
    -------
    Prefill the welcome screen data file field with the project sample dataset.

    Internal Logic
    ----------------
    1. If ``DEFAULT_DATA_PATH`` is set in ``.env`` and the file exists, use it.
    2. Else if the canonical ``G:\\...\\NST-EST2025-ALLDATA.csv`` exists, use it.
    3. Else use ``<repo>/sample/NST-EST2025-ALLDATA.csv``.

    Example invocation
    ------------------
    >>> p = default_sample_data_path()
    >>> p.name
    'NST-EST2025-ALLDATA.csv'
    """

    cfg = load_app_config()
    if cfg.default_data_path is not None and cfg.default_data_path.is_file():
        return cfg.default_data_path
    g_path = Path(r"G:\My Drive\AI_Projects\AI_FileAnalyze\sample\NST-EST2025-ALLDATA.csv")
    if g_path.exists():
        return g_path
    return _repo_root() / "sample" / "NST-EST2025-ALLDATA.csv"


def default_sample_meta_path(data_path: Path) -> Path:
    """Pick the default meta path (``.env`` override, legacy ``G:`` sample, or derived).

    Purpose
    -------
    Resolve metadata for the default data file on startup or in tools.

    Internal Logic
    ----------------
    1. If ``DEFAULT_META_PATH`` is set in ``.env`` and the path exists, use it.
    2. Else when data is the canonical ``G:`` NST sample and ``G:`` ``*_Meta`` exists, use that.
    3. Else return :func:`derive_meta_path` for ``data_path``.

    Example invocation
    ------------------
    >>> data = default_sample_data_path()
    >>> m = default_sample_meta_path(data)
    >>> m.name.endswith("_Meta")
    True
    """

    cfg = load_app_config()
    if cfg.default_meta_path is not None and cfg.default_meta_path.exists():
        return cfg.default_meta_path
    g_data = Path(r"G:\My Drive\AI_Projects\AI_FileAnalyze\sample\NST-EST2025-ALLDATA.csv")
    g_meta = Path(r"G:\My Drive\AI_Projects\AI_FileAnalyze\sample\NST-EST2025-ALLDATA.csv_Meta")
    try:
        if data_path.resolve() == g_data.resolve() and g_meta.exists():
            return g_meta
    except OSError:
        pass
    return derive_meta_path(data_path)


def templates_data_directory() -> Path:
    """Return the ``templates/`` folder under the repository root.

    Purpose
    -------
    ``Browse Templates`` lists regular files here (by stem) so users can pick a
    packaged dataset or preset without typing a path.

    Internal Logic
    ----------------
    Resolve :func:`_repo_root` / ``templates`` and create the directory when missing.

    Example invocation
    ------------------
    >>> p = templates_data_directory()
    >>> p.name == "templates"
    True
    """

    d = _repo_root() / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_template_stems_and_paths(templates_dir: Path) -> list[tuple[str, Path]]:
    """Collect ``(stem, path)`` pairs for template files (one path per stem).

    Purpose
    -------
    The picker shows **stems** (filename without extension) as the user requested.

    Internal Logic
    ----------------
    Iterate non-hidden files only; the first path encountered for each stem wins
    (paths are visited in sorted order).

    Example invocation
    ------------------
    >>> list_template_stems_and_paths(Path("/nonexistent"))  # doctest: +SKIP
    []
    """

    if not templates_dir.is_dir():
        return []
    by_stem: dict[str, Path] = {}
    for candidate in sorted(templates_dir.iterdir(), key=lambda p: p.name.lower()):
        if not candidate.is_file():
            continue
        if candidate.name.startswith("."):
            continue
        stem = candidate.stem
        if stem not in by_stem:
            by_stem[stem] = candidate
    return sorted(by_stem.items(), key=lambda kv: kv[0].lower())


def _apply_app_styles(app) -> None:
    """Apply the light pastel QSS theme to the app."""

    qss_path = _resolve_qss_path()
    try:
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    except Exception:
        # If QSS is unavailable, keep the default Qt theme instead of crashing.
        pass


def _maybe_set_window_icon(app, window) -> None:
    """Set the window and taskbar icon from bundled branding assets.

    Purpose
    -------
    Load the **File Analyzer** artwork so the title bar and (with AppUserModelID)
    Windows taskbar show the product icon instead of a generic Python icon.

    Internal Logic
    ---------------
    Prefer ``assets/icons/file_analyzer.ico`` (multi-resolution on Windows). If
    it is missing, fall back to ``assets/icons/file_analyzer.png``. No-op when
    neither file exists.

    Example invocation
    --------------------
    Called from ``build_welcome_window`` after creating ``QApplication`` and the
    main window::

        _maybe_set_window_icon(app, main_window)
    """

    try:
        from PyQt5.QtGui import QIcon
    except Exception:
        return

    assets_dir = Path(__file__).resolve().parents[3] / "assets"
    icons_dir = assets_dir / "icons"
    ico_path = icons_dir / "file_analyzer.ico"
    png_path = icons_dir / "file_analyzer.png"
    icon_path: Optional[Path] = None
    if ico_path.is_file():
        icon_path = ico_path
    elif png_path.is_file():
        icon_path = png_path
    if icon_path is None:
        return
    icon = QIcon(str(icon_path))
    app.setWindowIcon(icon)
    try:
        window.setWindowIcon(icon)
    except Exception:
        pass


def _set_windows_taskbar_app_id(app) -> None:
    """Set explicit Windows AppUserModelID (best-effort).

    Purpose
    -------
    Ensure taskbar grouping shows the correct icon, matching user expectations.
    """

    import sys

    if sys.platform != "win32":
        return

    app_id = "file-analyzer"
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        # Best-effort only.
        pass


def _parse_measure_decimal_places_text(raw: str) -> int:
    """Parse the **# of decimals** field from the main toolbar.

    Purpose
    -------
    Convert user text into an integer for :class:`~file_analyzer.ui.models.LoadedDatasetContext`
    and for rounding measure quick stats after load.

    Internal Logic
    ---------------
    Strip whitespace; empty string defaults to ``2``. Non-integers default to ``2``.
    Clamp the result to ``[0, 30]`` so pathological inputs cannot break formatting.

    Example invocation
    --------------------
    ``_parse_measure_decimal_places_text(" 3 ")`` → ``3``;
    ``_parse_measure_decimal_places_text("x")`` → ``2``.

    Args:
        raw: Raw contents of the decimals ``QLineEdit``.

    Returns:
        Number of fractional digits to use for measures and measure quick stats.
    """

    s = (raw or "").strip()
    if not s:
        return 2
    try:
        n = int(s)
    except ValueError:
        return 2
    return max(0, min(30, n))


class _LoadDatasetWorker:
    """Background worker: load dataset + compute quick stats."""

    def __init__(
        self,
        data_path: Path,
        delimiter: str,
        meta_path: Optional[Path] = None,
        measure_decimal_places: int = 2,
    ) -> None:
        self._data_path = data_path
        self._delimiter = delimiter
        self._meta_path = meta_path
        self._measure_decimal_places = measure_decimal_places

        self._config = load_app_config()

    def run(self) -> LoadedDatasetContext:
        """Load the dataset into DuckDB and compute quick stats."""

        meta_path = self._meta_path if self._meta_path is not None else derive_meta_path(self._data_path)
        meta = parse_meta_file(meta_path)

        session_cfg = DuckDBSessionConfig(
            temp_base_dir=self._config.temp_base_dir,
            duckdb_storage_mode=self._config.duckdb_storage_mode,
            duckdb_threads=self._config.duckdb_threads,
            cleanup_on_close=False,
        )
        session = DuckDBSession(meta=meta, config=session_cfg)
        try:
            session.load_csv_as_table(
                data_path=self._data_path,
                delimiter=self._delimiter,
                table_name="data",
                header=True,
            )

            db_path = session.database_path
            if db_path is None:
                raise RuntimeError("DuckDB database path is missing; expected file-backed mode.")

            quick_stats = compute_quick_stats_parallel(
                meta=meta,
                database_path=str(db_path),
                table_name="data",
                top_n=self._config.quick_stats_top_n,
                max_workers=self._config.quick_stats_max_workers,
            )
            quick_stats = apply_measure_decimal_rounding_to_quick_stats(
                quick_stats,
                self._measure_decimal_places,
            )

            session.close()
            return LoadedDatasetContext(
                meta=meta,
                database_path=db_path,
                temp_dir=session.temp_dir,
                quick_stats=quick_stats,
                source_data_path=self._data_path,
                source_delimiter=self._delimiter,
                table_name="data",
                measure_decimal_places=self._measure_decimal_places,
            )
        except Exception:
            # Ensure we close the session if an error occurs; we can safely cleanup here.
            session.close()
            raise


# ---- PyQt glue (imports kept inside to keep non-UI modules importable) ----


def build_welcome_window() -> None:
    """Create and show the File Analyzer application."""

    from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
    from PyQt5.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QProgressDialog,
        QSizePolicy,
        QTabWidget,
        QVBoxLayout,
        QWidget,
        QMainWindow,
    )

    from file_analyzer.ui.layout_persistence import (
        ensure_application_identity,
        restore_main_window_layout,
        restore_tab_widget_current_index,
        snapshot_all_layouts,
    )
    from file_analyzer.ui.grid_tab import DataGridTab
    from file_analyzer.ui.pivot_tab import PivotDataTab
    from file_analyzer.ui.summary_tab import SummaryTab
    from file_analyzer.ui.status_zoom import install_status_bar_zoom
    from file_analyzer.ui.visualize_tab import VisualizeTab

    ensure_application_identity()

    app = QApplication.instance() or QApplication([])
    _set_windows_taskbar_app_id(app)

    _apply_app_styles(app)

    class _FileAnalyzerMainWindow(QMainWindow):
        """Main shell that waits for tab background threads before closing.

        Purpose
        -------
        After **Load Data**, :class:`~file_analyzer.ui.grid_tab.DataGridTab` and
        :class:`~file_analyzer.ui.pivot_tab.PivotDataTab`, and
        :class:`~file_analyzer.ui.summary_tab.SummaryTab` start DuckDB queries on worker
        ``QThread`` objects immediately. Closing the window
        while that thread runs used to abort with *QThread: Destroyed while thread
        is still running*; this window coordinates an orderly wait.

        Internal Logic
        ---------------
        ``closeEvent`` walks ``DataGridTab`` and ``VisualizeTab`` children and calls
        each tab's ``wait_for_background_threads`` before the base implementation
        runs.
        """

        def closeEvent(self, event) -> None:
            """Wait for grid/chart/summary/export workers so ``QThread`` objects are not torn down mid-run."""

            fl = getattr(self, "_flush_tab_index_to_settings", None)
            if callable(fl):
                try:
                    fl()
                except Exception:
                    pass
            snapshot_all_layouts(self)
            for w in self.findChildren(DataGridTab):
                w.wait_for_background_threads()
            for w in self.findChildren(VisualizeTab):
                w.wait_for_background_threads()
            for w in self.findChildren(SummaryTab):
                w.wait_for_background_threads()
            super().closeEvent(event)

    main_window = _FileAnalyzerMainWindow()
    main_window.setWindowTitle(load_app_config().window_title)
    main_window._explicit_meta_path = None  # type: ignore[attr-defined]
    _maybe_set_window_icon(app, main_window)
    install_status_bar_zoom(app, main_window, base_point_size=10.0)
    restore_main_window_layout(main_window)

    # Hold load QThread across nested handlers so it is not GC'd while running;
    # on quit, wait for the worker so we never destroy QThread while still running.
    main_window._load_thread = None  # type: ignore[attr-defined]
    main_window._load_worker_qobj = None  # type: ignore[attr-defined]

    def _wait_all_background_threads_before_quit() -> None:
        """Block application shutdown until load and tab worker threads finish."""

        t = getattr(main_window, "_load_thread", None)
        if t is not None and t.isRunning():
            logging.getLogger(__name__).info(
                "Application quit requested while dataset load is running; waiting for worker."
            )
            t.wait()
        for w in main_window.findChildren(DataGridTab):
            w.wait_for_background_threads()
        for w in main_window.findChildren(VisualizeTab):
            w.wait_for_background_threads()
        for w in main_window.findChildren(SummaryTab):
            w.wait_for_background_threads()

    # ----- Top strip (menu bar area): paths + delimiter + submit -----
    menu_bar_host = QWidget()
    menu_layout = QVBoxLayout(menu_bar_host)
    menu_layout.setContentsMargins(8, 4, 8, 4)
    menu_layout.setSpacing(4)

    file_path = QLineEdit()
    file_path.setPlaceholderText("Data CSV / pipe file")
    file_path.setMinimumWidth(120)
    file_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    browse_btn = QPushButton("Browse data")
    browse_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    browse_meta_btn = QPushButton("Browse meta")
    browse_meta_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    browse_meta_btn.setToolTip(
        "Browse to a *_Meta file. If unset, the meta path is derived from the data file name."
    )

    delimiter_combo = QComboBox()
    delimiter_combo.addItems(["|", ",", "\\t"])
    delimiter_combo.setMaximumWidth(56)
    delimiter_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    _cfg = load_app_config()
    _default_data = default_sample_data_path()
    file_path.setText(str(_default_data))
    _default_meta = default_sample_meta_path(_default_data)
    if _cfg.default_meta_path is not None and _cfg.default_meta_path.exists():
        main_window._explicit_meta_path = _cfg.default_meta_path  # type: ignore[attr-defined]
        try:
            browse_meta_btn.setToolTip(str(_cfg.default_meta_path))
        except Exception:
            pass
    elif _default_meta.exists() and _default_meta != derive_meta_path(_default_data):
        main_window._explicit_meta_path = _default_meta  # type: ignore[attr-defined]
        try:
            browse_meta_btn.setToolTip(str(_default_meta))
        except Exception:
            pass
    delimiter_combo.setCurrentIndex(0)

    decimals_edit = QLineEdit("2")
    decimals_edit.setPlaceholderText("2")
    decimals_edit.setToolTip("Number of decimal places for measure fields and measure quick stats.")
    decimals_edit.setMaximumWidth(52)
    decimals_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    submit_btn = QPushButton("Load Data")
    submit_btn.setMinimumHeight(28)
    submit_btn.setEnabled(True)
    submit_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def labeled_field_row(label_text: str, field: QWidget, field_stretch: int = 0) -> QWidget:
        """Return a row with label text flush against the field (label ends with two spaces)."""

        wrap = QWidget()
        pair = QHBoxLayout(wrap)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(0)
        pair.addWidget(QLabel(label_text), 0)
        pair.addWidget(field, field_stretch)
        return wrap

    toolbar_row = QHBoxLayout()
    toolbar_row.setSpacing(6)
    toolbar_row.addWidget(labeled_field_row("Data:  ", file_path, 1), 1)
    toolbar_row.addWidget(browse_btn, 0)
    meta_label = QLabel("Meta:")
    meta_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    toolbar_row.addWidget(meta_label, 0)
    toolbar_row.addWidget(browse_meta_btn, 0)
    browse_templates_btn = QPushButton("Browse Templates")
    browse_templates_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    browse_templates_btn.setToolTip(
        "Pick a file from templates/: non-Excel files set the data path; .xlsx/.xls set Excel metadata."
    )
    toolbar_row.addWidget(browse_templates_btn, 0)
    toolbar_row.addWidget(labeled_field_row("Delim:  ", delimiter_combo, 0), 0)
    toolbar_row.addWidget(labeled_field_row("# of decimals:  ", decimals_edit, 0), 0)
    toolbar_row.addWidget(submit_btn, 0)
    menu_layout.addLayout(toolbar_row)

    main_window.setMenuWidget(menu_bar_host)

    # ----- Central tabs (placeholders until Load Data succeeds) -----
    tabs = QTabWidget()

    placeholder_visualize = QWidget()
    ph_v_layout = QVBoxLayout(placeholder_visualize)
    ph_v_layout.addStretch(1)
    lbl_v = QLabel("Load a dataset with Load Data above to populate this tab.")
    lbl_v.setAlignment(Qt.AlignCenter)
    lbl_v.setWordWrap(True)
    ph_v_layout.addWidget(lbl_v)
    ph_v_layout.addStretch(1)

    placeholder_grid = QWidget()
    ph_g_layout = QVBoxLayout(placeholder_grid)
    ph_g_layout.addStretch(1)
    lbl_g = QLabel("Load a dataset with Load Data above to populate this tab.")
    lbl_g.setAlignment(Qt.AlignCenter)
    lbl_g.setWordWrap(True)
    ph_g_layout.addWidget(lbl_g)
    ph_g_layout.addStretch(1)

    placeholder_pivot = QWidget()
    ph_p_layout = QVBoxLayout(placeholder_pivot)
    ph_p_layout.addStretch(1)
    lbl_p = QLabel("Load a dataset with Load Data above to populate this tab.")
    lbl_p.setAlignment(Qt.AlignCenter)
    lbl_p.setWordWrap(True)
    ph_p_layout.addWidget(lbl_p)
    ph_p_layout.addStretch(1)

    placeholder_summary = QWidget()
    ph_s_layout = QVBoxLayout(placeholder_summary)
    ph_s_layout.addStretch(1)
    lbl_s = QLabel("Load a dataset with Load Data above to populate this tab.")
    lbl_s.setAlignment(Qt.AlignCenter)
    lbl_s.setWordWrap(True)
    ph_s_layout.addWidget(lbl_s)
    ph_s_layout.addStretch(1)

    tabs.addTab(placeholder_summary, "Summary")
    tabs.addTab(placeholder_visualize, "Visualize")
    tabs.addTab(placeholder_grid, "Data Grid")
    tabs.addTab(placeholder_pivot, "Pivot Data")
    main_window.setCentralWidget(tabs)

    _tab_layout_timer = QTimer(main_window)
    _tab_layout_timer.setSingleShot(True)
    _tab_layout_timer.setInterval(200)

    def _persist_tab_index() -> None:
        """Write the active tab index so the next session reopens the same tab."""

        try:
            from PyQt5.QtCore import QSettings

            QSettings().setValue("ui/tabs_current_index", tabs.currentIndex())
        except Exception:
            pass

    _tab_layout_timer.timeout.connect(_persist_tab_index)

    def _on_tab_changed(_index: int) -> None:
        """Debounce tab-index writes while the user clicks through tabs quickly."""

        _tab_layout_timer.stop()
        _tab_layout_timer.start()

    try:
        tabs.currentChanged.connect(_on_tab_changed)  # type: ignore[attr-defined]
    except Exception:
        pass
    main_window._flush_tab_index_to_settings = _persist_tab_index  # type: ignore[attr-defined]
    restore_tab_widget_current_index(tabs)

    # ----- App-wide loading logic -----
    progress: Optional[QProgressDialog] = None

    class _WorkerQObject(QObject):
        """Runs :class:`_LoadDatasetWorker` on a ``QThread`` and stops the thread safely.

        Purpose
        -------
        Emit load results on the worker thread and always call :meth:`QThread.quit`
        from this thread's event loop (in ``finally``) so the thread can exit without
        relying on a main-thread slot. That avoids deadlock when the application
        quits while blocked in :meth:`QThread.wait` and the main event loop cannot
        yet deliver ``finished`` / ``failed``.

        Internal Logic
        ---------------
        ``run`` loads data, emits ``finished`` or ``failed``, then calls
        :meth:`QThread.quit` on the host thread in ``finally`` so that thread's
        event loop exits.

        Example invocation
        -------------------
        Not constructed directly; ``build_welcome_window`` wires::

            worker_qobj = _WorkerQObject(worker, thread)
            worker_qobj.moveToThread(thread)
        """

        finished = pyqtSignal(object)
        failed = pyqtSignal(str)

        def __init__(self, worker: _LoadDatasetWorker, host_thread: QThread) -> None:
            super().__init__()
            self._worker = worker
            self._host_thread = host_thread

        def run(self) -> None:
            """Execute the worker in its own QThread."""

            try:
                ctx = self._worker.run()
                self.finished.emit(ctx)
            except Exception as e:
                self.failed.emit(str(e))
            finally:
                self._host_thread.quit()

    browse_dialog_caption = "Select data file"

    def choose_file() -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            None,
            browse_dialog_caption,
            "",
            "Data files (*.csv *.txt);;All files (*)",
        )
        if path_str:
            file_path.setText(path_str)
            main_window._explicit_meta_path = None  # type: ignore[attr-defined]
            try:
                browse_meta_btn.setToolTip(
                    "Browse to a *_Meta file. If unset, the meta path is derived from the data file name."
                )
            except Exception:
                pass

    def choose_meta_file() -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            None,
            "Select metadata file",
            "",
            "Meta files (*_Meta * *.xlsx *.xls);;Excel (*.xlsx *.xls);;All files (*)",
        )
        if path_str:
            main_window._explicit_meta_path = Path(path_str)  # type: ignore[attr-defined]
            try:
                browse_meta_btn.setToolTip(str(main_window._explicit_meta_path))  # type: ignore[attr-defined]
            except Exception:
                pass

    def choose_template() -> None:
        """List ``templates/`` file stems in a searchable dialog and set the data path."""

        tdir = templates_data_directory()
        entries = list_template_stems_and_paths(tdir)
        if not entries:
            QMessageBox.information(
                main_window,
                "Browse Templates",
                f"No template files were found in:\n{tdir}",
            )
            return
        dlg = QDialog(main_window)
        dlg.setWindowTitle("Browse Templates")
        dlg.resize(440, 380)
        v = QVBoxLayout(dlg)
        search = QLineEdit()
        search.setPlaceholderText("Search templates…")
        lst = QListWidget()
        lst.setUniformItemSizes(True)
        for stem, fpath in entries:
            item = QListWidgetItem(stem)
            item.setData(Qt.UserRole, fpath)  # type: ignore[attr-defined]
            lst.addItem(item)

        def refilter(text: str) -> None:
            """Hide list rows that do not match the search box (case-insensitive)."""

            q = text.strip().lower()
            for idx in range(lst.count()):
                row = lst.item(idx)
                row.setHidden(bool(q) and q not in row.text().lower())
            cur = lst.currentItem()
            if cur is None or cur.isHidden():
                for idx in range(lst.count()):
                    vis = lst.item(idx)
                    if not vis.isHidden():
                        lst.setCurrentItem(vis)
                        break
            sync_ok_button()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)  # type: ignore[attr-defined]
        ok_widget = buttons.button(QDialogButtonBox.Ok)  # type: ignore[attr-defined]
        ok_widget.setEnabled(False)

        def sync_ok_button() -> None:
            """Enable OK only when a visible row is selected."""

            current = lst.currentItem()
            ok_widget.setEnabled(current is not None and not current.isHidden())

        lst.currentItemChanged.connect(lambda *_a: sync_ok_button())  # type: ignore[attr-defined]

        def accept_if_item(_item: object) -> None:
            """Double-click applies the template immediately."""

            dlg.accept()

        lst.itemDoubleClicked.connect(accept_if_item)  # type: ignore[attr-defined]
        buttons.accepted.connect(dlg.accept)  # type: ignore[attr-defined]
        buttons.rejected.connect(dlg.reject)  # type: ignore[attr-defined]
        search.textChanged.connect(refilter)  # type: ignore[attr-defined]
        v.addWidget(search)
        v.addWidget(lst, 1)
        v.addWidget(buttons)
        lst.setCurrentRow(0)
        sync_ok_button()
        if dlg.exec_() != QDialog.Accepted:  # type: ignore[attr-defined]
            return
        chosen = lst.currentItem()
        if chosen is None or chosen.isHidden():
            return
        fp = chosen.data(Qt.UserRole)  # type: ignore[attr-defined]
        if fp is None:
            return
        fp_path = Path(str(fp))
        if fp_path.suffix.lower() in (".xlsx", ".xls"):
            main_window._explicit_meta_path = fp_path  # type: ignore[attr-defined]
            try:
                browse_meta_btn.setToolTip(f"Excel metadata template: {fp_path}")
            except Exception:
                pass
        else:
            file_path.setText(str(fp_path))
            main_window._explicit_meta_path = None  # type: ignore[attr-defined]
            try:
                browse_meta_btn.setToolTip(
                    "Browse to a *_Meta file. If unset, the meta path is derived from the data file name."
                )
            except Exception:
                pass

    def start_load() -> None:
        nonlocal progress

        existing = getattr(main_window, "_load_thread", None)
        if existing is not None and existing.isRunning():
            QMessageBox.information(
                main_window,
                "Busy",
                "A load is already in progress. Please wait for it to finish.",
            )
            return

        data_path_str = file_path.text().strip()
        if not data_path_str:
            QMessageBox.warning(main_window, "Missing input", "Please select a data file first.")
            return

        data_path = Path(data_path_str)
        if not data_path.exists():
            QMessageBox.critical(main_window, "File not found", f"File does not exist:\n{data_path}")
            return

        explicit_meta: Optional[Path] = getattr(main_window, "_explicit_meta_path", None)  # type: ignore[attr-defined]
        meta_path = explicit_meta if explicit_meta is not None else derive_meta_path(data_path)
        if explicit_meta is not None and not explicit_meta.exists():
            QMessageBox.critical(
                main_window,
                "File not found",
                f"Meta file does not exist:\n{explicit_meta}",
            )
            return
        if meta_path.exists():
            meta_warn = validate_meta_file_before_load(meta_path)
            if meta_warn:
                QMessageBox.warning(main_window, "Metadata columns required", meta_warn)
                return

        delimiter_ui = delimiter_combo.currentText()
        delimiter = "\t" if delimiter_ui == "\\t" else delimiter_ui
        measure_decimals = _parse_measure_decimal_places_text(decimals_edit.text())

        submit_btn.setEnabled(False)
        file_path.setEnabled(False)
        browse_btn.setEnabled(False)
        browse_meta_btn.setEnabled(False)
        browse_templates_btn.setEnabled(False)
        delimiter_combo.setEnabled(False)
        decimals_edit.setEnabled(False)

        progress = QProgressDialog("Loading and analyzing file...", "Cancel", 0, 0, main_window)
        progress.setWindowModality(True)
        progress.setAutoClose(True)
        progress.setMinimumDuration(0)

        worker = _LoadDatasetWorker(
            data_path=data_path,
            delimiter=delimiter,
            meta_path=explicit_meta,
            measure_decimal_places=measure_decimals,
        )
        thread = QThread()
        worker_qobj = _WorkerQObject(worker, thread)
        worker_qobj.moveToThread(thread)
        main_window._load_thread = thread  # type: ignore[attr-defined]
        main_window._load_worker_qobj = worker_qobj  # type: ignore[attr-defined]

        def on_finished(ctx: LoadedDatasetContext) -> None:
            nonlocal progress
            if progress is not None:
                progress.cancel()
            thread.wait()
            main_window._load_thread = None  # type: ignore[attr-defined]
            main_window._load_worker_qobj = None  # type: ignore[attr-defined]

            for w in main_window.findChildren(DataGridTab):
                w.wait_for_background_threads()
            for w in main_window.findChildren(VisualizeTab):
                w.wait_for_background_threads()
            for w in main_window.findChildren(SummaryTab):
                w.wait_for_background_threads()

            reload_snapshot: Optional[dict] = None
            try:
                snap_parts: dict = {}
                for w in main_window.findChildren(DataGridTab):
                    if getattr(w, "_ui_surface", None) == "grid":
                        snap_parts["grid"] = w.export_reload_session_state()
                    elif getattr(w, "_ui_surface", None) == "pivot":
                        snap_parts["pivot"] = w.export_reload_session_state()
                for w in main_window.findChildren(VisualizeTab):
                    snap_parts["visualize"] = w.export_reload_session_state()
                if snap_parts:
                    reload_snapshot = snap_parts
            except Exception:
                reload_snapshot = None

            tabs.clear()
            visualize_tab = VisualizeTab(ctx)
            grid_tab = DataGridTab(ctx)
            pivot_tab = PivotDataTab(ctx)
            summary_tab = SummaryTab(ctx)
            try:
                visualize_tab.bind_grid_tab(grid_tab)
                visualize_tab.bind_pivot_tab(pivot_tab)
            except Exception:
                # Bridge binding is best-effort; UI should still render.
                pass
            tabs.addTab(summary_tab, "Summary")
            tabs.addTab(visualize_tab, "Visualize")
            tabs.addTab(grid_tab, "Data Grid")
            tabs.addTab(pivot_tab, "Pivot Data")
            restore_tab_widget_current_index(tabs)

            if reload_snapshot is not None:
                try:
                    g = reload_snapshot.get("grid")
                    if isinstance(g, dict):
                        grid_tab.import_reload_session_state(g)
                    p = reload_snapshot.get("pivot")
                    if isinstance(p, dict):
                        pivot_tab.import_reload_session_state(p)
                    v = reload_snapshot.get("visualize")
                    if isinstance(v, dict):
                        visualize_tab.import_reload_session_state(v)
                except Exception:
                    pass

            submit_btn.setEnabled(True)
            file_path.setEnabled(True)
            browse_btn.setEnabled(True)
            browse_meta_btn.setEnabled(True)
            browse_templates_btn.setEnabled(True)
            delimiter_combo.setEnabled(True)
            decimals_edit.setEnabled(True)

        def on_failed(msg: str) -> None:
            if progress is not None:
                progress.cancel()
            thread.wait()
            main_window._load_thread = None  # type: ignore[attr-defined]
            main_window._load_worker_qobj = None  # type: ignore[attr-defined]
            QMessageBox.critical(main_window, "Load failed", msg)
            submit_btn.setEnabled(True)
            file_path.setEnabled(True)
            browse_btn.setEnabled(True)
            browse_meta_btn.setEnabled(True)
            browse_templates_btn.setEnabled(True)
            delimiter_combo.setEnabled(True)
            decimals_edit.setEnabled(True)

        thread.started.connect(worker_qobj.run)
        worker_qobj.finished.connect(on_finished)
        worker_qobj.failed.connect(on_failed)

        thread.start()

    browse_btn.clicked.connect(choose_file)
    browse_meta_btn.clicked.connect(choose_meta_file)
    browse_templates_btn.clicked.connect(choose_template)
    submit_btn.clicked.connect(start_load)

    main_window.show()

    app.exec_()


__all__ = [
    "build_welcome_window",
    "default_sample_data_path",
    "default_sample_meta_path",
    "derive_meta_path",
    "list_template_stems_and_paths",
    "templates_data_directory",
]

