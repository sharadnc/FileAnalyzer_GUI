"""Environment configuration loader for File Analyzer.

Purpose
-------
Centralize environment-driven configuration for both the GUI and non-GUI parts
of the project. Environment variables are loaded from the first existing file among:
``<project>/.env``, ``../venv/.env``, and ``../.venv/.env``.

Internal Logic
---------------
1. Locate the project root (directory containing ``pyproject.toml``).
2. Load the first ``.env`` candidate (project root preferred).
3. Provide typed getters with safe defaults, including optional
   ``DEFAULT_DATA_PATH`` and ``DEFAULT_META_PATH`` for the welcome screen.

This module does not require PyQt or DuckDB to be installed.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv as _load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional until pip install completes
    _load_dotenv = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class AppConfig:
    """Typed application configuration for File Analyzer.

    Purpose
    -------
    Provide a stable set of settings used across modules.

    Internal Logic
    ---------------
    Values are loaded from environment variables, using defaults when the
    variables are missing. The intent is to keep the app runnable without
    requiring a pre-created `.env` file during development.

    ``quick_stats_max_workers`` caps the thread pool used while computing
    per-field hover statistics after load (see ``QUICK_STATS_MAX_WORKERS``).

    ``default_data_path`` / ``default_meta_path`` prefill the welcome screen from
    ``DEFAULT_DATA_PATH`` and ``DEFAULT_META_PATH`` in the project ``.env`` file.

    ``venv_python`` is the optional ``VENV_PYTHON`` path (for documentation; launch
    scripts read the same variable from ``.env``).

    ``window_title`` comes from ``WINDOW_TITLE`` (main window title bar text).
    """

    page_size_default: int
    quick_stats_top_n: int
    quick_stats_max_workers: int
    duckdb_threads: Optional[int]
    temp_base_dir: Path
    duckdb_storage_mode: str
    default_data_path: Optional[Path]
    default_meta_path: Optional[Path]
    venv_python: Optional[Path]
    window_title: str


def _project_root() -> Path:
    """Return the repository root path.

    Returns
    -------
    Path
        Directory containing ``pyproject.toml``.
    """

    # config.py: src/file_analyzer/config.py
    return Path(__file__).resolve().parents[2]


def load_app_config() -> AppConfig:
    """Load application configuration from ``.env`` and environment variables.

    Parameters
    ----------
    None

    Returns
    -------
    AppConfig
        Typed configuration.
    """

    root = _project_root()
    env_candidates = [
        root / ".env",
        root.parent / "venv" / ".env",
        root.parent / ".venv" / ".env",
    ]

    for candidate in env_candidates:
        if candidate.exists():
            if _load_dotenv is not None:
                _load_dotenv(str(candidate))
            else:
                # Without python-dotenv, skip file loading; process env vars still apply.
                import logging

                logging.getLogger(__name__).debug(
                    "python-dotenv not installed; skipping .env file %s", candidate
                )
            break

    page_size_default = int_from_env("PAGE_SIZE_DEFAULT", default=100)
    quick_stats_top_n = int_from_env("QUICK_STATS_TOP_N", default=15)

    cpus = os.cpu_count() or 4
    quick_stats_default_workers = max(4, min(32, cpus * 2))
    quick_stats_max_workers = int_from_env(
        "QUICK_STATS_MAX_WORKERS",
        default=quick_stats_default_workers,
    )
    quick_stats_max_workers = max(1, min(64, quick_stats_max_workers))

    duckdb_threads = env_optional_int("DUCKDB_THREADS")
    import tempfile

    temp_base_dir_str = env_str("TEMP_BASE_DIR", default=tempfile.gettempdir())
    temp_base_dir = Path(temp_base_dir_str)

    duckdb_storage_mode = env_str("DUCKDB_STORAGE_MODE", default="file")
    default_data_path = env_optional_path("DEFAULT_DATA_PATH", base_dir=root)
    default_meta_path = env_optional_path("DEFAULT_META_PATH", base_dir=root)
    venv_python = env_optional_path("VENV_PYTHON", base_dir=root)
    window_title = env_str("WINDOW_TITLE", default="File Analyzer").strip() or "File Analyzer"
    return AppConfig(
        page_size_default=page_size_default,
        quick_stats_top_n=quick_stats_top_n,
        quick_stats_max_workers=quick_stats_max_workers,
        duckdb_threads=duckdb_threads,
        temp_base_dir=temp_base_dir,
        duckdb_storage_mode=duckdb_storage_mode,
        default_data_path=default_data_path,
        default_meta_path=default_meta_path,
        venv_python=venv_python,
        window_title=window_title,
    )


def env_str(name: str, default: str) -> str:
    """Read a string environment variable with a default."""

    import os

    value = os.getenv(name)
    return value if value is not None else default


def int_from_env(name: str, default: int) -> int:
    """Read an integer environment variable with a default."""

    import os

    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def env_optional_int(name: str) -> Optional[int]:
    """Read an optional integer environment variable."""

    import os

    value = os.getenv(name)
    if value is None:
        return None
    return int(value)


def env_optional_path(name: str, *, base_dir: Path) -> Optional[Path]:
    """Read an optional filesystem path from the environment.

    Purpose
    -------
    Support ``DEFAULT_DATA_PATH`` and ``DEFAULT_META_PATH`` in the project ``.env``
    file. Relative paths resolve against ``base_dir`` (the repository root).

    Internal Logic
    ----------------
    1. Return ``None`` when the variable is unset or blank.
    2. Strip optional surrounding quotes from the value.
    3. If the path is not absolute, join with ``base_dir`` and ``resolve()``.

    Example invocation
    --------------------
    ``env_optional_path(\"DEFAULT_DATA_PATH\", base_dir=root)``
  """

    import os

    raw = os.getenv(name)
    if raw is None:
        return None
    text = raw.strip().strip('"').strip("'")
    if not text:
        return None
    p = Path(text)
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    else:
        p = p.resolve()
    return p

