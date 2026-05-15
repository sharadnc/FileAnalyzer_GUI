"""Quick statistics computation for File Analyzer.

Purpose
-------
Provide fast, metadata-driven descriptive statistics that the UI uses for:
- hover tooltips (field descriptions and summary stats),
- chart/table previews,
- sensible default chart behavior.

Internal Logic
---------------
Given a dataset loaded into DuckDB, we compute field-wise aggregates:

1. For ``Char``/dimension-like fields: top-N value frequencies.
2. For ``Num``/measure fields: min, max, sum, mean, median.
3. For ``Date``/``Datetime`` fields: min and max.

Each field’s aggregates run in a worker thread: workers open their own DuckDB
connections (session-safe because each session uses its own database file
under the per-session temp directory). A :class:`~concurrent.futures.ThreadPoolExecutor`
schedules one task per eligible field so hover quick stats finish quickly after
load.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Literal, Optional, Tuple

from file_analyzer.meta_parser import FieldMeta, MetaDefinition, is_display_only_field, is_yyyymmdd_field_type


@dataclass(frozen=True)
class FieldQuickStats:
    """Quick statistics summary for one field.

    Purpose
    -------
    Provide a UI-friendly bundle of precomputed stats per field.

    Internal Logic
    ---------------
    Instances are created by worker functions inside
    :func:`compute_quick_stats_parallel` from DuckDB query results.

    Parameters
    ----------
    field:
        The field metadata object used to generate stats.
    char_frequencies:
        Optional list of (value, count) tuples for Char fields.
    numeric_summary:
        Optional numeric stats bundle for Num fields.
    min_value:
        Optional minimum value for Date/Datetime fields.
    max_value:
        Optional maximum value for Date/Datetime fields.
    """

    field: FieldMeta
    char_frequencies: Optional[List[Tuple[str, int]]] = None
    numeric_summary: Optional[Dict[str, float]] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None


def _format_nullable_datetime_value(value: Any) -> Optional[str]:
    """Convert a DuckDB datetime-like value into a stable string."""

    if value is None:
        return None
    return str(value)


def _compute_char_stats(
    database_path: str,
    table_name: str,
    field: FieldMeta,
    top_n: int,
) -> FieldQuickStats:
    """Compute top-N value frequencies for a Char/dimension field."""

    import duckdb  # type: ignore

    col = field.name.replace('"', '""')
    conn = duckdb.connect(database=database_path)
    try:
        rows = conn.execute(
            f"""
            SELECT {col} AS value, COUNT(*) AS cnt
            FROM {table_name}
            GROUP BY {col}
            ORDER BY cnt DESC
            LIMIT {int(top_n)}
            """
        ).fetchall()
    finally:
        conn.close()

    freqs: List[Tuple[str, int]] = []
    for value, cnt in rows:
        freqs.append((str(value), int(cnt)))

    return FieldQuickStats(field=field, char_frequencies=freqs)


def _compute_num_stats(
    database_path: str,
    table_name: str,
    field: FieldMeta,
) -> FieldQuickStats:
    """Compute numeric summary statistics for a Num/measure field."""

    import duckdb  # type: ignore

    col = field.name.replace('"', '""')
    conn = duckdb.connect(database=database_path)
    try:
        row = conn.execute(
            f"""
            SELECT
              MIN({col}) AS min_value,
              MAX({col}) AS max_value,
              SUM({col}) AS sum_value,
              AVG({col}) AS mean_value,
              median({col}) AS median_value
            FROM {table_name}
            """
        ).fetchone()
    finally:
        conn.close()

    min_value, max_value, sum_value, mean_value, median_value = row
    numeric_summary: Dict[str, float] = {
        "min": float(min_value) if min_value is not None else float("nan"),
        "max": float(max_value) if max_value is not None else float("nan"),
        "sum": float(sum_value) if sum_value is not None else float("nan"),
        "mean": float(mean_value) if mean_value is not None else float("nan"),
        "median": float(median_value) if median_value is not None else float("nan"),
    }

    return FieldQuickStats(field=field, numeric_summary=numeric_summary)


def _compute_date_stats(
    database_path: str,
    table_name: str,
    field: FieldMeta,
) -> FieldQuickStats:
    """Compute min/max for a Date/Datetime field."""

    import duckdb  # type: ignore

    col = field.name.replace('"', '""')
    conn = duckdb.connect(database=database_path)
    try:
        row = conn.execute(
            f"""
            SELECT MIN({col}) AS min_value, MAX({col}) AS max_value
            FROM {table_name}
            """
        ).fetchone()
    finally:
        conn.close()

    min_value, max_value = row
    return FieldQuickStats(
        field=field,
        min_value=_format_nullable_datetime_value(min_value),
        max_value=_format_nullable_datetime_value(max_value),
    )


def compute_quick_stats_parallel(
    meta: MetaDefinition,
    database_path: Optional[str],
    table_name: str = "data",
    top_n: int = 15,
    max_workers: int = 8,
) -> Dict[str, FieldQuickStats]:
    """Compute quick descriptive stats for all fields using a thread pool.

    Purpose
    -------
    Precompute per-field aggregates in parallel so Visualize hover tooltips and
    other UI surfaces can read stats immediately after a dataset profile is
    loaded—without blocking the UI thread (callers typically invoke this from a
    background worker).

    Internal Logic
    ---------------
    1. Classify each :class:`FieldMeta` into Char / Num / Date / skip from
       ``field_type`` (``Char``/``Num``/``Date`` plus common synonyms such as
       ``VARCHAR``, ``TEXT``, ``INTEGER``, ``TIMESTAMP``).
    2. Submit one DuckDB-backed worker per eligible field to
       :class:`~concurrent.futures.ThreadPoolExecutor`.
    3. Use ``as_completed`` to merge each finished :class:`FieldQuickStats`
       into a dict keyed by field name.
    4. Pool size is ``min(max_workers, number of tasks)`` so we do not spawn
       idle threads.

    Parameters
    ----------
    meta:
        Parsed metadata.
    database_path:
        DuckDB database file path. Required because each worker opens its own
        read-only connection to the same file-backed session database.
    table_name:
        Table containing the loaded dataset.
    top_n:
        Number of most frequent values to keep for Char fields.
    max_workers:
        Upper bound on concurrent worker threads (also capped by task count).

    Returns
    -------
    Dict[str, FieldQuickStats]
        Field name -> quick stats.

    Raises
    ------
    ValueError
        If ``database_path`` is missing (parallel execution requires a shared DB file).

    Example invocation
    --------------------
    From a loader worker after CSV import::

        stats = compute_quick_stats_parallel(
            meta=meta,
            database_path=str(session.database_path),
            table_name="data",
            top_n=15,
            max_workers=12,
        )
    """

    if database_path is None:
        raise ValueError(
            "database_path is required for compute_quick_stats_parallel. "
            "Use file-backed DuckDB sessions."
        )

    log = logging.getLogger(__name__)

    def type_bucket(field: FieldMeta) -> Literal["char", "num", "date", "skip"]:
        """Bucket a field based on its FieldType (includes common Excel synonyms)."""

        if is_display_only_field(field):
            return "skip"
        if is_yyyymmdd_field_type(field.field_type):
            return "char"
        t = field.field_type.lower().strip()
        if t in (
            "char",
            "varchar",
            "nvarchar",
            "text",
            "string",
            "str",
            "character",
            "alpha",
        ):
            return "char"
        if t in (
            "num",
            "number",
            "numeric",
            "integer",
            "int",
            "float",
            "double",
            "decimal",
            "real",
            "bigint",
            "smallint",
            "long",
            "money",
            "currency",
        ):
            return "num"
        if t in ("date", "datetime", "timestamp", "time"):
            return "date"
        return "skip"

    task_count = 0
    for field in meta.fields:
        if type_bucket(field) != "skip":
            task_count += 1

    if task_count == 0:
        log.info("Quick stats: no eligible fields; returning empty stats map.")
        return {}

    pool_cap = max(1, min(max(1, max_workers), task_count))
    futures: list[Future[FieldQuickStats]] = []
    t0 = time.perf_counter()
    log.info(
        "Quick stats: running %s field task(s) with up to %s worker thread(s)",
        task_count,
        pool_cap,
    )

    results: Dict[str, FieldQuickStats] = {}
    with ThreadPoolExecutor(max_workers=pool_cap, thread_name_prefix="fa-qstats") as executor:
        for field in meta.fields:
            bucket = type_bucket(field)
            if bucket == "char":
                futures.append(
                    executor.submit(_compute_char_stats, database_path, table_name, field, top_n)
                )
            elif bucket == "num":
                futures.append(executor.submit(_compute_num_stats, database_path, table_name, field))
            elif bucket == "date":
                futures.append(executor.submit(_compute_date_stats, database_path, table_name, field))
            else:
                continue

        for fut in as_completed(futures):
            stats = fut.result()
            results[stats.field.name] = stats

    elapsed = time.perf_counter() - t0
    log.info(
        "Quick stats: finished %s field(s) in %.2fs (threads=%s)",
        len(results),
        elapsed,
        pool_cap,
    )
    return results


def _round_measure_scalar(value: float, decimal_places: int) -> float:
    """Round one floating summary value for measure quick stats.

    Purpose
    -------
    Apply the user-selected decimal count to min/max/sum/mean/median without
    turning ``NaN`` into a bogus number.

    Internal Logic
    ---------------
    If ``value`` is not finite, return it unchanged; otherwise ``round`` to
    ``decimal_places`` (already clamped by the caller).

    Example invocation
    --------------------
    ``_round_measure_scalar(3.14159, 2)`` → ``3.14``; ``_round_measure_scalar(float('nan'), 2)`` → ``nan``.
    """

    if value != value or value in (float("inf"), float("-inf")):
        return value
    return round(float(value), int(decimal_places))


def apply_measure_decimal_rounding_to_quick_stats(
    quick_stats: Dict[str, FieldQuickStats],
    decimal_places: int,
) -> Dict[str, FieldQuickStats]:
    """Return a new stats map with measure (``M``) numeric summaries rounded.

    Purpose
    -------
    After :func:`compute_quick_stats_parallel`, the UI may need aggregates shown
    with a fixed number of fractional digits; this copies each
    :class:`FieldQuickStats` and replaces ``numeric_summary`` values only for
    measure fields.

    Internal Logic
    ---------------
    Clamp ``decimal_places`` to ``[0, 30]``. For each entry with
    ``field.field_dtype == "M"`` and a non-``None`` ``numeric_summary``, build a
    new dict of rounded floats via :func:`_round_measure_scalar` and
    :func:`dataclasses.replace`.

    Example invocation
    --------------------
    ``rounded = apply_measure_decimal_rounding_to_quick_stats(raw_stats, 2)``.

    Args:
        quick_stats: Field name to stats as returned from parallel computation.
        decimal_places: Desired number of digits after the decimal point.

    Returns:
        New mapping (shallow copy of keys; new :class:`FieldQuickStats` for
        updated measures).
    """

    dp = max(0, min(30, int(decimal_places)))
    out: Dict[str, FieldQuickStats] = {}
    for name, st in quick_stats.items():
        if st.numeric_summary is None or st.field.field_dtype != "M":
            out[name] = st
            continue
        rounded_summary = {
            key: _round_measure_scalar(float(val), dp) for key, val in st.numeric_summary.items()
        }
        out[name] = replace(st, numeric_summary=rounded_summary)
    return out

