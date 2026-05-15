"""Tests for DEFAULT_DATA_PATH / DEFAULT_META_PATH from .env."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_analyzer.config import load_app_config
from file_analyzer.ui.welcome import default_sample_data_path, default_sample_meta_path


def test_default_paths_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Purpose: absolute paths in the environment are returned as defaults."""

    data = tmp_path / "sample" / "data.txt"
    data.parent.mkdir(parents=True)
    data.write_text("x", encoding="utf-8")
    meta = tmp_path / "templates" / "meta.xlsx"
    meta.parent.mkdir(parents=True)
    meta.write_bytes(b"x")

    monkeypatch.setenv("DEFAULT_DATA_PATH", str(data))
    monkeypatch.setenv("DEFAULT_META_PATH", str(meta))

    cfg = load_app_config()
    assert cfg.default_data_path == data.resolve()
    assert cfg.default_meta_path == meta.resolve()
    assert default_sample_data_path() == data.resolve()
    assert default_sample_meta_path(data) == meta.resolve()
