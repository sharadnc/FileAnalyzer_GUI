"""Sanity checks for chart-table pagination math (mirrors Data Grid page slicing)."""

from __future__ import annotations

import math


def test_page_count_and_last_page_start() -> None:
    """250 rows at 100/page → 3 pages; last page starts at index 200."""

    total = 250
    ps = 100
    pages = max(1, math.ceil(total / ps)) if total else 1
    assert pages == 3
    max_page = max(0, math.ceil(total / ps) - 1) if total else 0
    assert max_page == 2
    start = max_page * ps
    assert start == 200
    end = min(start + ps, total)
    assert end - start == 50
