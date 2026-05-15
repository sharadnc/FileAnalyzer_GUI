"""Step-by-step verification for File Analyzer (no GUI interaction).

Purpose
-------
Mirror the default-file workflow (paths → load worker → tab construction) so CI
or a developer can see exactly which step fails without clicking through the UI.

Internal Logic
---------------
1. Resolve default sample paths (same helpers as the welcome screen).
2. Verify files exist on disk.
3. Run :class:`file_analyzer.ui.welcome._LoadDatasetWorker` synchronously.
4. Import ``VisualizeTab`` before creating ``QApplication`` (Qt WebEngine rule).
5. Build ``QApplication`` (offscreen), construct both tabs, bind bridge.

Example invocation
--------------------
From the repository root::

    py -3 tools/verify_app_steps.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    """Run verification steps; return 0 on success, 1 on failure."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("verify_app_steps")
    root = _repo_root()
    src = root / "src"
    sys.path.insert(0, str(src))

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    step = 0

    def ok(msg: str) -> None:
        nonlocal step
        step += 1
        log.info("Step %s OK: %s", step, msg)

    def fail(msg: str) -> int:
        nonlocal step
        step += 1
        log.error("Step %s FAIL: %s", step, msg)
        return 1

    # Step: default paths
    from file_analyzer.ui.welcome import (
        _LoadDatasetWorker,
        default_sample_data_path,
        default_sample_meta_path,
    )

    data_path = default_sample_data_path()
    meta_path = default_sample_meta_path(data_path)
    ok(f"defaults data={data_path} meta={meta_path}")

    if not data_path.is_file():
        return fail(f"data file missing: {data_path}")
    ok("data file exists")

    if not meta_path.is_file():
        return fail(f"meta file missing: {meta_path}")
    ok("meta file exists")

    # Step: synchronous load (same as worker thread body)
    try:
        ctx = _LoadDatasetWorker(
            data_path=data_path,
            delimiter="|",
            meta_path=meta_path,
            measure_decimal_places=2,
        ).run()
    except Exception as e:
        return fail(f"_LoadDatasetWorker.run: {e}")
    ok(f"LoadedDatasetContext quick_stats keys={len(ctx.quick_stats)}")

    # Step: WebEngine import order
    import file_analyzer.ui.visualize_tab  # noqa: F401

    ok("imported visualize_tab before QApplication")

    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    ok("QApplication created")

    from file_analyzer.ui.grid_tab import DataGridTab
    from file_analyzer.ui.pivot_tab import PivotDataTab
    from file_analyzer.ui.visualize_tab import VisualizeTab

    try:
        v = VisualizeTab(ctx)
        g = DataGridTab(ctx)
        p = PivotDataTab(ctx)
        v.bind_grid_tab(g)
        v.bind_pivot_tab(p)
    except Exception as e:
        return fail(f"tab construction: {e}")
    ok("VisualizeTab + DataGridTab + PivotDataTab constructed and bound")

    # Let the initial async queries finish (same as user seeing each tab fill).
    for tab in (g, p):
        if tab._grid_thread is not None and tab._grid_thread.isRunning():
            tab._grid_thread.wait(120_000)
        if tab._grid_thread is not None and tab._grid_thread.isRunning():
            return fail("grid/pivot background thread still running after wait")
    ok("grid/pivot background threads finished (or were not used)")

    log.info("All steps passed (%s).", step)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
