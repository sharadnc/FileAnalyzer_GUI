"""Word/Excel-style UI zoom on the main window status bar.

Purpose
-------
Provide a bottom-right **Zoom** slider (with percentage label) that scales the
default Qt font for the whole application so labels, inputs, tables, and tabs
grow or shrink together—similar to the zoom control in Microsoft Word or Excel
on Windows 11.

Internal Logic
---------------
- Install a ``QStatusBar`` on the given ``QMainWindow`` with ``setSizeGripEnabled(True)``
  so the window shows the familiar resize corner next to the zoom cluster.
- Add a permanent widget (placed rightmost when added first) with **2px** spacing
  between the **Zoom** label, horizontal ``QSlider``, and percent label.
- On ``valueChanged`` (after the initial programmatic value), build a new ``QFont``
  with the same family as the baseline and ``pointSizeF = base_point_size * (percent / 100)``,
  then call ``QApplication.setFont`` and refresh top-level widgets. A short debounced
  dialog reminds the user to click **Load Data** so tables reflow; ``welcome`` restores
  filter/pivot/visualize shelf state across that reload.
- The global QSS must not pin ``font-size`` on ``QWidget`` so inherited font sizes
  follow the application font (see ``styles.qss``).

Example invocation
--------------------
Called once at startup from ``build_welcome_window``::

    install_status_bar_zoom(app, main_window, base_point_size=10.0)
"""

from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)


def install_status_bar_zoom(
    app: Any,
    main_window: Any,
    *,
    base_point_size: float = 10.0,
    min_percent: int = 75,
    max_percent: int = 200,
    default_percent: int = 100,
) -> None:
    """Attach a status bar with a bottom-right zoom slider to ``main_window``.

    Purpose
    -------
    Wire the zoom UI and keep a stable baseline point size so scaling is
    predictable regardless of the OS default font.

    Internal Logic
    ---------------
    Import PyQt5 classes lazily. Create ``QStatusBar``, style it lightly for a
    neutral shell look, build ``QSlider`` in ``[min_percent, max_percent]`` with
    ``default_percent``, connect ``valueChanged`` to a closure that applies
    ``QApplication.setFont`` and nudges ``update`` on the main window.

    Args:
        app: Active ``QApplication`` instance.
        main_window: ``QMainWindow`` that will own the status bar.
        base_point_size: Unscaled UI font size in points at ``100%`` zoom.
        min_percent: Slider minimum (whole percent).
        max_percent: Slider maximum (whole percent).
        default_percent: Initial slider value.

    Example invocation
    --------------------
    ``install_status_bar_zoom(QApplication.instance(), win, base_point_size=10.0)``
    """

    from PyQt5.QtCore import Qt, QTimer  # type: ignore[import-not-found]
    from PyQt5.QtGui import QFont  # type: ignore[import-not-found]
    from PyQt5.QtWidgets import (  # type: ignore[import-not-found]
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QSlider,
        QStatusBar,
        QWidget,
    )

    base_pt: float = float(base_point_size)
    if base_pt <= 0:
        base_pt = 10.0

    # Baseline family follows the stylesheet preference.
    base_family: str = "Segoe UI"

    def apply_zoom_percent(percent: int) -> None:
        """Scale the application default font and refresh visible chrome."""

        try:
            pct = int(percent)
        except (TypeError, ValueError):
            pct = default_percent
        pct = max(min_percent, min(max_percent, pct))
        factor = pct / 100.0
        new_pt = max(6.0, min(36.0, base_pt * factor))
        f = QFont(base_family)
        f.setPointSizeF(float(new_pt))
        app.setFont(f)
        try:
            main_window.update()
        except Exception:
            pass
        # Encourage repaint of already-created widgets (tabs may have cached metrics).
        try:
            for w in app.allWidgets():
                try:
                    w.update()
                except Exception:
                    continue
        except Exception as exc:
            _LOG.debug("Zoom refresh walk skipped: %s", exc)

    status = QStatusBar(main_window)
    try:
        status.setSizeGripEnabled(True)
    except Exception:
        pass
    status.setStyleSheet(
        """
        QStatusBar {
            background-color: #F3F3F3;
            color: #1F1F1F;
            border-top: 1px solid #D0D0D0;
            min-height: 28px;
            padding-left: 8px;
            padding-right: 6px;
        }
        """
    )
    main_window.setStatusBar(status)

    zoom_host = QWidget()
    zoom_row = QHBoxLayout(zoom_host)
    zoom_row.setContentsMargins(0, 0, 0, 0)
    # Tight cluster: label, slider, percent (two px between adjacent widgets).
    zoom_row.setSpacing(2)

    zoom_caption = QLabel("Zoom")
    try:
        zoom_caption.setStyleSheet("color: #444444; font-weight: 500;")
    except Exception:
        pass

    initial_pct = int(default_percent)
    try:
        from PyQt5.QtCore import QSettings  # type: ignore[import-not-found]

        initial_pct = int(QSettings().value("ui/zoom_percent", default_percent))
    except Exception:
        initial_pct = int(default_percent)
    initial_pct = max(int(min_percent), min(int(max_percent), initial_pct))

    slider = QSlider(Qt.Horizontal)  # type: ignore[attr-defined]
    slider.setMinimum(int(min_percent))
    slider.setMaximum(int(max_percent))
    slider.setSingleStep(5)
    slider.setPageStep(10)
    slider.setFixedWidth(160)
    slider.setToolTip("Adjust text size for the whole application (like Word/Excel zoom).")
    slider.setStyleSheet(
        """
        QSlider::groove:horizontal {
            height: 4px;
            background: #E5E5E5;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #0F6CBD;
            border: 1px solid #0B5CAD;
            width: 10px;
            height: 18px;
            margin: -8px 0;
            border-radius: 3px;
        }
        QSlider::sub-page:horizontal {
            background: #C7E0F4;
            border-radius: 2px;
        }
        """
    )

    pct_label = QLabel(f"{int(initial_pct)}%")
    pct_label.setMinimumWidth(40)
    pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)  # type: ignore[attr-defined]
    try:
        pct_label.setStyleSheet("color: #1F1F1F; font-weight: 600;")
    except Exception:
        pass

    def on_zoom_changed(value: int) -> None:
        """Update the percent label, scale fonts, and persist the zoom percent."""

        pct_label.setText(f"{int(value)}%")
        apply_zoom_percent(int(value))
        try:
            from PyQt5.QtCore import QSettings  # type: ignore[import-not-found]

            QSettings().setValue("ui/zoom_percent", int(value))
        except Exception:
            pass

    zoom_notice_timer = QTimer(main_window)
    zoom_notice_timer.setSingleShot(True)
    zoom_notice_timer.setInterval(450)

    def show_zoom_reload_notice() -> None:
        """Tell the user to reload so table metrics and splitters match the new zoom."""

        try:
            QMessageBox.information(
                main_window,
                "Zoom changed",
                "Click <b>Load Data</b> again to refresh tables and keep column spacing "
                "aligned with the new zoom. Your paths, delimiter, filters, and tab "
                "selections are restored on reload.",
            )
        except Exception:
            pass

    zoom_notice_timer.timeout.connect(show_zoom_reload_notice)  # type: ignore[attr-defined]

    def on_zoom_changed_with_notice(value: int) -> None:
        """Apply zoom immediately, persist, and debounce the Load Data reminder."""

        on_zoom_changed(int(value))
        zoom_notice_timer.stop()
        zoom_notice_timer.start()

    zoom_row.addWidget(zoom_caption, 0)
    zoom_row.addWidget(slider, 0)
    zoom_row.addWidget(pct_label, 0)

    # First permanent widget sits at the far right (next to the size grip).
    status.addPermanentWidget(zoom_host, 0)

    try:
        main_window._ui_zoom_slider = slider  # type: ignore[attr-defined]
    except Exception:
        pass

    # Initial value: avoid valueChanged so we do not pop a notice before any user action.
    try:
        slider.blockSignals(True)
        slider.setValue(int(initial_pct))
    finally:
        slider.blockSignals(False)
    slider.valueChanged.connect(on_zoom_changed_with_notice)  # type: ignore[attr-defined]

    # Establish baseline font immediately so the UI matches the slider.
    apply_zoom_percent(int(slider.value()))
