"""Pivot Data tab: Data Grid filters with an Excel-style pivot summary table.

This module exposes :class:`PivotDataTab`, a thin specialization of
:class:`~file_analyzer.ui.grid_tab.DataGridTab` that uses the shared filter chrome
and replaces the paginated raw grid with a pivot field bar plus a single result
table. See :meth:`file_analyzer.ui.grid_tab.DataGridTab.__init__` ``ui_surface`` flag.
"""

from __future__ import annotations

from file_analyzer.ui.grid_tab import DataGridTab
from file_analyzer.ui.models import LoadedDatasetContext


class PivotDataTab(DataGridTab):
    """Same filter and toolbar affordances as the Data Grid, with a pivot summary table.

    Purpose
    -------
    Let users narrow data with the familiar dimension/measure filter panels and
    ``Apply Filters``, then arrange row/column dimensions and an aggregated measure
    in a layout similar to Excel pivot fields.

    Internal Logic
    ---------------
    Delegates entirely to :class:`~file_analyzer.ui.grid_tab.DataGridTab` with
    ``ui_surface="pivot"``, which swaps the lower splitter pane for pivot controls
    plus one ``QTableWidget`` fed by DuckDB ``GROUP BY`` / ``PIVOT`` queries.

    Example invocation
    --------------------
    After loading a dataset::

        pivot_tab = PivotDataTab(ctx)
        tabs.addTab(pivot_tab, "Pivot Data")
    """

    def __init__(self, ctx: LoadedDatasetContext) -> None:
        """Create the tab using the pivot surface of the shared grid implementation."""

        super().__init__(ctx, ui_surface="pivot")


__all__ = ["PivotDataTab"]
