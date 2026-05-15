"""Pytest configuration for the File Analyzer project."""

from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure() -> None:
    """Ensure `src/` is on `sys.path` for test imports.

    Purpose
    -------
    Allow tests to import ``file_analyzer`` without requiring `pip install -e .`.

    Internal Logic
    ---------------
    Insert the repository's ``src`` directory at the front of ``sys.path``.
    """

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    sys.path.insert(0, str(src_dir))

