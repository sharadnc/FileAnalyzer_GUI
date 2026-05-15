"""UI-facing models shared between screens and tabs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from file_analyzer.meta_parser import MetaDefinition
from file_analyzer.stats_service import FieldQuickStats


@dataclass(frozen=True)
class LoadedDatasetContext:
    """Context object created after loading a dataset.

    Purpose
    -------
    Provide a single, immutable payload that both the Visualize tab and the
    Data Grid tab can use without re-reading the input file.

    Internal Logic
    ---------------
    The application creates a per-session DuckDB database file in a unique temp
    directory. We store:
    - its path so later operations can open additional connections safely, and
    - the meta and precomputed quick stats for immediate UI feedback.

    Parameters
    ----------
    meta:
        Parsed metadata definition.
    database_path:
        DuckDB database file path.
    temp_dir:
        Temp directory containing per-session files.
    quick_stats:
        Mapping of field name to quick stats.
    source_data_path:
        Original file path used for CSV import (for profiling labels and file size).
    source_delimiter:
        Delimiter character passed to the loader (pipe, comma, tab, etc.).
    table_name:
        DuckDB table name containing the loaded dataset.
    measure_decimal_places:
        Number of fractional digits used when rounding measure (``M``) quick stats
        and when formatting measure values in the Visualize and Data Grid tabs.
    """

    meta: MetaDefinition
    database_path: Path
    temp_dir: Path
    quick_stats: Dict[str, FieldQuickStats]
    source_data_path: Path
    source_delimiter: str
    table_name: str = "data"
    measure_decimal_places: int = 2

