"""Persist window geometry, tab index, and :class:`QSplitter` states across sessions.

Purpose
-------
Users resize panes (filters, charts, tables, summary header vs body). Store those
layouts in :class:`QSettings` so the next application launch restores them.

Internal Logic
---------------
- Rely on ``QCoreApplication`` organization/application names set via
  :func:`ensure_application_identity` before the first ``QApplication`` / ``QSettings``.
- Splitters that opt in use an ``objectName`` prefix :data:`LAYOUT_SPLITTER_PREFIX`;
  :func:`snapshot_all_layouts` walks the main window and saves ``saveState()`` bytes.
- :func:`restore_main_window_layout` applies saved ``QMainWindow`` geometry (and
  optional ``saveState``) before the window is shown.

Example invocation
--------------------
From ``closeEvent``::

    snapshot_all_layouts(main_window)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

_LOG = logging.getLogger(__name__)

LAYOUT_SPLITTER_PREFIX: str = "fa_layout_"

try:
    from PyQt5.QtCore import QCoreApplication, QSettings, QTimer
    from PyQt5.QtWidgets import QMainWindow, QSplitter, QTabWidget, QWidget
except ModuleNotFoundError:  # pragma: no cover
    QCoreApplication = object  # type: ignore[assignment]
    QSettings = object  # type: ignore[assignment]
    QTimer = object  # type: ignore[assignment]
    QMainWindow = object  # type: ignore[assignment]
    QSplitter = object  # type: ignore[assignment]
    QTabWidget = object  # type: ignore[assignment]
    QWidget = object  # type: ignore[assignment]


def ensure_application_identity() -> None:
    """Register org/app keys used by :class:`QSettings` (call before ``QApplication``).

    Purpose
    -------
    On Windows, ``QSettings`` maps to the registry / Ini paths keyed by organization
    and application name. This must be set early for consistent storage.

    Internal Logic
    ----------------
    Assign :meth:`QCoreApplication.setOrganizationName` and ``setApplicationName``
    when ``QCoreApplication`` instance exists or as static defaults before the
    first ``QApplication`` construction.

    Example invocation
    ------------------
    ``ensure_application_identity()`` at the top of ``build_welcome_window``.
    """

    try:
        QCoreApplication.setOrganizationName("FileAnalyzer")
        QCoreApplication.setApplicationName("File Analyzer")
    except Exception as exc:
        _LOG.debug("ensure_application_identity: %s", exc)


def restore_splitter_state(settings: QSettings, splitter: QSplitter, object_name: str) -> None:
    """Apply a stored ``QSplitter.saveState`` payload when present.

    Purpose
    -------
    Reapply pixel sizes from the user's last session for one splitter.

    Internal Logic
    ----------------
    Read ``splitters/<object_name>``; call :meth:`QSplitter.restoreState` on success.

    Example invocation
    ------------------
    ``restore_splitter_state(QSettings(), splitter, "fa_layout_viz_main")``
    """

    if not object_name.startswith(LAYOUT_SPLITTER_PREFIX):
        return
    key = f"splitters/{object_name}"
    try:
        raw: Any = settings.value(key)
    except Exception:
        return
    if raw is None:
        return
    try:
        splitter.restoreState(raw)
    except Exception as exc:
        _LOG.debug("restore_splitter_state %s: %s", object_name, exc)


def save_splitter_state(settings: QSettings, splitter: QSplitter, object_name: str) -> None:
    """Persist one splitter's ``saveState`` under ``splitters/<object_name>``."""

    if not object_name.startswith(LAYOUT_SPLITTER_PREFIX):
        return
    try:
        settings.setValue(f"splitters/{object_name}", splitter.saveState())
    except Exception as exc:
        _LOG.debug("save_splitter_state %s: %s", object_name, exc)


def wire_splitter_autosave(splitter: QSplitter, object_name: str, parent: QWidget) -> None:
    """Debounce-save a splitter whenever the user drags a handle.

    Purpose
    -------
    Crash-safe persistence: layout updates without waiting for window close.

    Internal Logic
    ----------------
    Attach ``splitterMoved`` to a single-shot ``QTimer``; on fire, open ``QSettings``
    and :func:`save_splitter_state`.

    Example invocation
    ------------------
    ``wire_splitter_autosave(self._main_splitter, "fa_layout_viz_main", self)``
    """

    if not object_name.startswith(LAYOUT_SPLITTER_PREFIX):
        return
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(450)

    def _flush() -> None:
        """Write the splitter state to disk."""

        try:
            s = QSettings()
            save_splitter_state(s, splitter, object_name)
            s.sync()
        except Exception as exc:
            _LOG.debug("wire_splitter_autosave flush: %s", exc)

    timer.timeout.connect(_flush)

    def _on_move(_pos: int, _index: int) -> None:
        """Restart the debounce timer on each drag event."""

        timer.stop()
        timer.start()

    try:
        splitter.splitterMoved.connect(_on_move)  # type: ignore[attr-defined]
    except Exception as exc:
        _LOG.debug("wire_splitter_autosave connect: %s", exc)


def restore_main_window_layout(main_window: QMainWindow, *, default_width: int = 1200, default_height: int = 800) -> None:
    """Restore saved geometry (and optional window state) or apply defaults.

    Purpose
    -------
    Bring back the user's last window size/position on cold start.

    Internal Logic
    ----------------
    Try ``main/geometry`` then ``main/state``; fall back to :meth:`QWidget.resize`.

    Example invocation
    ------------------
    ``restore_main_window_layout(main_window)`` before ``main_window.show()``.
    """

    try:
        s = QSettings()
    except Exception:
        main_window.resize(default_width, default_height)
        return
    geom = s.value("main/geometry")
    try:
        if geom is not None and main_window.restoreGeometry(geom):
            pass
        else:
            main_window.resize(default_width, default_height)
    except Exception:
        main_window.resize(default_width, default_height)
    st = s.value("main/state")
    if st is not None:
        try:
            main_window.restoreState(st)
        except Exception as exc:
            _LOG.debug("restoreState skipped: %s", exc)


def restore_tab_widget_current_index(tabs: QTabWidget) -> None:
    """Select the last saved tab index if it is still in range."""

    try:
        s = QSettings()
        idx = int(s.value("ui/tabs_current_index", 0))
    except Exception:
        idx = 0
    try:
        n = tabs.count()
        if n <= 0:
            return
        tabs.setCurrentIndex(min(max(0, idx), n - 1))
    except Exception as exc:
        _LOG.debug("restore_tab_widget_current_index: %s", exc)


def snapshot_all_layouts(main_window: QMainWindow) -> None:
    """Save geometry, window state, tab index, zoom, and every registered splitter.

    Purpose
    -------
    Invoked from the main window ``closeEvent`` (and optionally ``aboutToQuit``)
    so the next launch matches the user's layout.

    Internal Logic
    ----------------
    Serialize ``QMainWindow`` geometry/state, central :class:`QTabWidget` index,
    optional ``_ui_zoom_slider`` value, and each child :class:`QSplitter` whose
    ``objectName`` starts with :data:`LAYOUT_SPLITTER_PREFIX`.

    Example invocation
    ------------------
    ``snapshot_all_layouts(self)`` inside ``closeEvent`` before the base implementation.
    """

    try:
        s = QSettings()
    except Exception:
        return
    try:
        s.setValue("main/geometry", main_window.saveGeometry())
        s.setValue("main/state", main_window.saveState())
    except Exception as exc:
        _LOG.debug("snapshot geometry: %s", exc)
    cw = main_window.centralWidget()
    if isinstance(cw, QTabWidget):
        try:
            s.setValue("ui/tabs_current_index", cw.currentIndex())
        except Exception as exc:
            _LOG.debug("snapshot tab index: %s", exc)
    for sp in main_window.findChildren(QSplitter):
        try:
            oid = sp.objectName()
        except Exception:
            continue
        if isinstance(oid, str) and oid.startswith(LAYOUT_SPLITTER_PREFIX):
            save_splitter_state(s, sp, oid)
    z = getattr(main_window, "_ui_zoom_slider", None)
    if z is not None:
        try:
            s.setValue("ui/zoom_percent", int(z.value()))
        except Exception as exc:
            _LOG.debug("snapshot zoom: %s", exc)
    try:
        s.sync()
    except Exception:
        pass


__all__ = [
    "LAYOUT_SPLITTER_PREFIX",
    "ensure_application_identity",
    "restore_splitter_state",
    "save_splitter_state",
    "wire_splitter_autosave",
    "restore_main_window_layout",
    "restore_tab_widget_current_index",
    "snapshot_all_layouts",
]
