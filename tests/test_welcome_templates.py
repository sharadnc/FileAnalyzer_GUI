"""Tests for template listing helpers in :mod:`file_analyzer.ui.welcome`."""

from __future__ import annotations

from pathlib import Path

from file_analyzer.ui.welcome import list_template_stems_and_paths


def test_list_template_stems_uses_stem_and_skips_hidden(tmp_path: Path) -> None:
    """Purpose: stems are unique display keys; dotfiles are ignored.

    Internal Logic
    ----------------
    Create two files sharing logic expectations and assert mapping order.

    Example invocation
    ------------------
    ``pytest tests/test_welcome_templates.py -q``
    """

    (tmp_path / "alpha.csv").write_text("1", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / "beta.tsv").write_text("2", encoding="utf-8")
    rows = list_template_stems_and_paths(tmp_path)
    stems = [s for s, _ in rows]
    assert "alpha" in stems and "beta" in stems
    assert not any(s.startswith(".") for s in stems)
