"""Tests for Visualize tab chart color palette."""

from __future__ import annotations

from file_analyzer.ui.visualize_tab import _TABLEAU10_PALETTE, _default_chart_palette


def test_default_chart_palette_nonempty_unique() -> None:
    pal = _default_chart_palette()
    assert len(pal) >= 10
    assert len(pal) == len(set(pal))


def test_tableau_fallback_has_ten_colors() -> None:
    assert len(_TABLEAU10_PALETTE) == 10
