"""Application entrypoint for File Analyzer (PyQt5 GUI)."""

from __future__ import annotations

import sys
import traceback


def _notify_startup_failure(exc: BaseException) -> None:
    """Show a visible error when the GUI fails before or outside a Qt event loop.

    Purpose
    -------
    When the app is started with ``pythonw`` or a hidden launcher, tracebacks are
    otherwise invisible and the process appears to exit instantly.

    Internal Logic
    ---------------
    On Windows, call ``MessageBoxW`` with the exception and limited traceback text.
    Else print the same text to ``stderr``.

    Example invocation
    --------------------
    ``try: main() except Exception as e: _notify_startup_failure(e)``
    """

    text = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
    if len(text) > 3000:
        text = text[:2997] + "..."

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                0,
                text,
                "File Analyzer — startup error",
                0x10,
            )
            return
        except Exception:
            pass

    print(text, file=sys.stderr)


def main() -> None:
    """Run the File Analyzer GUI.

    Purpose
    -------
    Provide a stable entrypoint so users can launch the app with:
    ``python src/main.py`` or via a packaging launcher.

    Internal Logic
    ---------------
    Calls :func:`file_analyzer.ui.welcome.build_welcome_window`.
    """

    try:
        from file_analyzer.ui.welcome import build_welcome_window
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Failed to import GUI components. Install the UI dependencies: "
            '`pip install "file-analyzer[ui]"` (from the project folder, with your venv active).'
        ) from e

    build_welcome_window()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _notify_startup_failure(e)
        raise SystemExit(1) from e
