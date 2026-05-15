"""Dataset-wide summary reports for the Summary tab (profiling + quality metrics).

Purpose
-------
After data is loaded into DuckDB, compute per-field inference, missingness,
descriptive statistics, histogram bins, IQR outliers, and duplicate-row counts
for display in the UI without blocking the main thread (callers run this from a
worker).

Internal Logic
---------------
- Open a read-only DuckDB connection to the session database.
- Run compact SQL aggregates per field (quoted identifiers for reserved names).
- Classify each column into semantic buckets (boolean / numeric / string /
  datetime) using metadata hints plus lightweight casts on samples.
- Duplicate rows: ``SUM(cnt - 1)`` over ``GROUP BY`` all columns (full-row match).

Example invocation
--------------------
``report = compute_dataset_summary_report(str(ctx.database_path), ctx.table_name, ctx.meta)``
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Literal, Optional, Sequence, Tuple

from file_analyzer.meta_parser import FieldMeta, MetaDefinition

_BOOL_TOKENS = frozenset(
    {
        "0",
        "1",
        "t",
        "f",
        "true",
        "false",
        "yes",
        "no",
        "y",
        "n",
    }
)


def _format_edge_no_sci(value: float) -> str:
    """Format a finite float without scientific notation (trim trailing zeros).

    Purpose
    -------
    Histogram bin labels must stay human-readable (no ``1e+06`` style).

    Internal Logic
    ---------------
    Convert via :class:`decimal.Decimal` ``str`` then ``format(..., 'f')``, strip
    trailing fractional zeros.

    Example invocation
    --------------------
    ``_format_edge_no_sci(1234567.89)`` → ``\"1234567.89\"``.
    """

    if not math.isfinite(value):
        return str(value)
    dec = Decimal(str(value))
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text else "0"


def _bin_range_label(lo: float, hi: float) -> str:
    """Build a bin label ``low - high`` using spaces around the dash."""

    return f"{_format_edge_no_sci(lo)} - {_format_edge_no_sci(hi)}"


def _duck_quote(ident: str) -> str:
    """Return a double-quoted DuckDB identifier."""

    return '"' + str(ident).replace('"', '""') + '"'


def _sql_list_idents(names: Sequence[str]) -> str:
    """Comma-separated quoted identifiers for ``GROUP BY`` / ``SELECT`` lists."""

    return ", ".join(_duck_quote(n) for n in names)


@dataclass(frozen=True)
class FieldSummaryReport:
    """Structured profiling payload for one column."""

    field_name: str
    meta_field_type: str
    field_dtype: Literal["D", "M"]
    inferred_semantic: Literal["boolean", "numeric", "string", "datetime", "mixed"]
    duckdb_typeof_sample: str
    row_count: int
    null_count: int
    empty_string_count: int
    missing_pct: float
    distinct_count: int
    unique_pct: float
    is_high_cardinality: bool
    numeric_min: Optional[float]
    numeric_max: Optional[float]
    numeric_mean: Optional[float]
    numeric_median: Optional[float]
    numeric_stddev_pop: Optional[float]
    numeric_variance_pop: Optional[float]
    top_string_values: List[Tuple[str, int]] = field(default_factory=list)
    histogram_bins: List[Tuple[str, int]] = field(default_factory=list)
    outlier_count_iqr: Optional[int] = None
    outlier_pct: Optional[float] = None


@dataclass(frozen=True)
class DatasetDuplicateReport:
    """How many rows share an identical full-row fingerprint."""

    total_rows: int
    duplicate_extra_rows: int
    duplicate_pct: float
    distinct_full_rows: int


@dataclass(frozen=True)
class DatasetSummaryReport:
    """Complete summary tab payload."""

    fields: List[FieldSummaryReport]
    duplicates: DatasetDuplicateReport


def _infer_semantic(
    *,
    field: FieldMeta,
    distinct_count: int,
    bool_like_ratio: float,
    numeric_parse_ratio: float,
    timestamp_parse_ratio: float,
) -> Literal["boolean", "numeric", "string", "datetime", "mixed"]:
    """Pick a semantic label from metadata plus parse ratios.

    Purpose
    -------
    Map each column into boolean / numeric / string / datetime / mixed for the
    Summary tab header.

    Internal Logic
    ---------------
    **Dimension (``D``)** fields never resolve to ``numeric``—they stay string-like
    unless boolean or datetime heuristics fire. **Measure (``M``)** fields follow
    numeric rules when casts agree; ``mixed`` flags metadata/parse disagreements.
    """

    ft = field.field_type.lower()
    if distinct_count <= 2 and bool_like_ratio >= 0.95 and distinct_count > 0:
        return "boolean"
    if field.field_dtype == "D":
        if timestamp_parse_ratio >= 0.9:
            return "datetime"
        return "string"
    if field.field_dtype == "M" or numeric_parse_ratio >= 0.95:
        if ft in ("char",) and numeric_parse_ratio < 0.5:
            return "mixed"
        return "numeric"
    if ft in ("num", "integer", "float", "decimal") and numeric_parse_ratio < 0.5:
        return "mixed"
    return "string"


def _compute_bool_like_ratio(conn: object, table: str, col_q: str) -> float:
    """Share of non-null values that normalize to boolean-like tokens."""

    literals = ", ".join("'" + t.replace("'", "''") + "'" for t in sorted(_BOOL_TOKENS))
    row = conn.execute(
        f"""
        SELECT
          SUM(CASE WHEN {col_q} IS NULL THEN 0 ELSE 1 END) AS nn,
          SUM(
            CASE
              WHEN {col_q} IS NULL THEN 0
              WHEN lower(trim(cast({col_q} AS VARCHAR))) IN ({literals})
              THEN 1 ELSE 0
            END
          ) AS ok
        FROM {table}
        """
    ).fetchone()
    nn, ok = int(row[0] or 0), int(row[1] or 0)
    if nn == 0:
        return 0.0
    return ok / nn


def _compute_parse_ratios(conn: object, table: str, col_q: str) -> Tuple[float, float]:
    """Return (numeric_parse_ratio, timestamp_parse_ratio) over non-null cells."""

    row = conn.execute(
        f"""
        SELECT
          SUM(CASE WHEN {col_q} IS NULL THEN 0 ELSE 1 END) AS nn,
          SUM(
            CASE
              WHEN {col_q} IS NULL THEN 0
              WHEN try_cast({col_q} AS DOUBLE) IS NOT NULL THEN 1 ELSE 0
            END
          ) AS nnum,
          SUM(
            CASE
              WHEN {col_q} IS NULL THEN 0
              WHEN try_cast(trim(cast({col_q} AS VARCHAR)) AS TIMESTAMP) IS NOT NULL THEN 1
              ELSE 0
            END
          ) AS nts
        FROM {table}
        """
    ).fetchone()
    nn = int(row[0] or 0)
    if nn == 0:
        return 0.0, 0.0
    return float(row[1] or 0) / nn, float(row[2] or 0) / nn


def _typeof_sample(conn: object, table: str, col_q: str) -> str:
    """Return ``typeof`` for the first non-null cell (or ``'NULL'``)."""

    row = conn.execute(
        f"SELECT typeof({col_q}) FROM {table} WHERE {col_q} IS NOT NULL LIMIT 1"
    ).fetchone()
    if not row:
        return "NULL"
    return str(row[0])


def _field_base_metrics(conn: object, table: str, col_q: str) -> Tuple[int, int, int, int]:
    """Return (row_count, null_count, empty_trim_count, distinct_count)."""

    row = conn.execute(
        f"""
        SELECT
          COUNT(*) AS n,
          SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) AS nnull,
          SUM(
            CASE
              WHEN {col_q} IS NULL THEN 0
              WHEN trim(cast({col_q} AS VARCHAR)) = '' THEN 1 ELSE 0
            END
          ) AS nempty,
          COUNT(DISTINCT {col_q}) AS ndist
        FROM {table}
        """
    ).fetchone()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


def _numeric_block(conn: object, table: str, col_q: str) -> Tuple[Optional[float], ...]:
    """Min, max, mean, median, stddev_pop, var_pop for values that cast to DOUBLE."""

    row = conn.execute(
        f"""
        SELECT
          MIN(try_cast({col_q} AS DOUBLE)),
          MAX(try_cast({col_q} AS DOUBLE)),
          AVG(try_cast({col_q} AS DOUBLE)),
          median(try_cast({col_q} AS DOUBLE)),
          stddev_pop(try_cast({col_q} AS DOUBLE)),
          var_pop(try_cast({col_q} AS DOUBLE))
        FROM {table}
        """
    ).fetchone()

    def f(x: object) -> Optional[float]:
        if x is None:
            return None
        v = float(x)
        return v if math.isfinite(v) else None

    return tuple(f(x) for x in row)


def _histogram_raw_counts(conn: object, table: str, col_q: str, bins: int) -> List[Tuple[float, float, int]]:
    """Equal-width histogram counts as ``(low_edge, high_edge, count)`` tuples."""

    bounds = conn.execute(
        f"""
        SELECT
          MIN(try_cast({col_q} AS DOUBLE)) AS mn,
          MAX(try_cast({col_q} AS DOUBLE)) AS mx
        FROM {table}
        WHERE try_cast({col_q} AS DOUBLE) IS NOT NULL
        """
    ).fetchone()
    if not bounds or bounds[0] is None or bounds[1] is None:
        return []
    mn, mx = float(bounds[0]), float(bounds[1])
    if not math.isfinite(mn) or not math.isfinite(mx):
        return []
    cnt_row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE try_cast({col_q} AS DOUBLE) IS NOT NULL"
    ).fetchone()
    nn = int(cnt_row[0] or 0)
    if nn == 0:
        return []
    if mn == mx:
        return [(mn, mx, nn)]

    b = max(1, int(bins))
    span = mx - mn
    rows = conn.execute(
        f"""
        WITH stats AS (
          SELECT
            MIN(try_cast({col_q} AS DOUBLE)) AS mn,
            MAX(try_cast({col_q} AS DOUBLE)) AS mx
          FROM {table}
          WHERE try_cast({col_q} AS DOUBLE) IS NOT NULL
        ),
        bucketed AS (
          SELECT
            LEAST(
              {b - 1},
              GREATEST(
                0,
                CAST(
                  FLOOR(
                    (try_cast({col_q} AS DOUBLE) - s.mn)
                    / NULLIF((s.mx - s.mn) / {b}.0, 0)
                  ) AS INTEGER
                )
              )
            ) AS bidx
          FROM {table}
          CROSS JOIN stats AS s
          WHERE try_cast({col_q} AS DOUBLE) IS NOT NULL
        )
        SELECT bidx, COUNT(*) AS c
        FROM bucketed
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    width = span / b
    out: List[Tuple[float, float, int]] = []
    for bidx, c in rows:
        bi = int(bidx)
        lo = mn + bi * width
        hi = lo + width
        out.append((lo, hi, int(c)))
    return out


def _measure_range_distribution(
    conn: object,
    table: str,
    col_q: str,
    row_count: int,
    *,
    fine_bins: int = 200,
    top_show: int = 50,
) -> List[Tuple[str, int]]:
    """Up to ``top_show`` densest numeric bins, ``Others``, optional non-numeric row, ``Total``."""

    raw = _histogram_raw_counts(conn, table, col_q, fine_bins)
    if not raw:
        return [("Total", row_count)]

    nn = sum(c for _, _, c in raw)
    scored = sorted([(lo, hi, c) for lo, hi, c in raw], key=lambda t: (-t[2], t[0]))
    top = scored[: int(top_show)]
    rest = sum(int(t[2]) for t in scored[int(top_show) :])

    out: List[Tuple[str, int]] = []
    for lo, hi, c in top:
        out.append((_bin_range_label(lo, hi), int(c)))
    if rest > 0:
        out.append(("Others", int(rest)))
    remainder = int(row_count) - int(nn)
    if remainder > 0:
        out.append(("(null or not numeric)", remainder))
    out.append(("Total", int(row_count)))
    return out


def _dimension_value_distribution(
    conn: object,
    table: str,
    col_q: str,
    row_count: int,
    top_n: int = 50,
) -> List[Tuple[str, int]]:
    """Top ``top_n`` string values by frequency, ``Others``, and ``Total`` (= row count)."""

    tn = int(top_n)
    others_row = conn.execute(
        f"""
        WITH g AS (
          SELECT COALESCE(CAST({col_q} AS VARCHAR), '(null)') AS v, COUNT(*) AS c
          FROM {table}
          GROUP BY 1
        ),
        r AS (
          SELECT v, c, ROW_NUMBER() OVER (ORDER BY c DESC, v ASC) AS rn
          FROM g
        )
        SELECT COALESCE(SUM(c), 0) AS oc
        FROM r
        WHERE rn > {tn}
        """
    ).fetchone()
    others_c = int(others_row[0] or 0)

    top_rows = conn.execute(
        f"""
        WITH g AS (
          SELECT COALESCE(CAST({col_q} AS VARCHAR), '(null)') AS v, COUNT(*) AS c
          FROM {table}
          GROUP BY 1
        ),
        r AS (
          SELECT v, c, ROW_NUMBER() OVER (ORDER BY c DESC, v ASC) AS rn
          FROM g
        )
        SELECT v, c
        FROM r
        WHERE rn <= {tn}
        ORDER BY c DESC, v ASC
        """
    ).fetchall()

    out: List[Tuple[str, int]] = [(str(v), int(c)) for v, c in top_rows]
    if others_c > 0:
        out.append(("Others", others_c))
    out.append(("Total", int(row_count)))
    return out


def _outlier_iqr(conn: object, table: str, col_q: str) -> Tuple[Optional[int], Optional[float]]:
    """Count rows outside Tukey fences on DOUBLE cast."""

    row2 = conn.execute(
        f"""
        WITH d AS (
          SELECT try_cast({col_q} AS DOUBLE) AS x
          FROM {table}
        ),
        s AS (
          SELECT quantile_cont(x, 0.25) AS q1, quantile_cont(x, 0.75) AS q3
          FROM d WHERE x IS NOT NULL
        ),
        flagged AS (
          SELECT
            d.x,
            s.q1,
            s.q3,
            (s.q3 - s.q1) AS iqr
          FROM d
          CROSS JOIN s
          WHERE d.x IS NOT NULL
        )
        SELECT
          COUNT(*) AS nn,
          SUM(CASE WHEN x < q1 - 1.5 * iqr OR x > q3 + 1.5 * iqr THEN 1 ELSE 0 END) AS nout
        FROM flagged
        """
    ).fetchone()
    nn = int(row2[0] or 0)
    nout = int(row2[1] or 0)
    if nn == 0:
        return None, None
    return nout, 100.0 * nout / nn


def _summarize_field(conn: object, table: str, field: FieldMeta) -> FieldSummaryReport:
    """Compute all sections for a single :class:`FieldMeta`."""

    col_q = _duck_quote(field.name)
    row_count, null_count, empty_string_count, distinct_count = _field_base_metrics(conn, table, col_q)
    missing_denom = row_count if row_count else 1
    missing_pct = 100.0 * (null_count + empty_string_count) / missing_denom
    unique_pct = 100.0 * distinct_count / missing_denom if row_count else 0.0
    is_high_cardinality = distinct_count > max(50, int(0.2 * row_count)) if row_count else False

    bool_like = _compute_bool_like_ratio(conn, table, col_q)
    num_r, ts_r = _compute_parse_ratios(conn, table, col_q)
    inferred = _infer_semantic(
        field=field,
        distinct_count=distinct_count,
        bool_like_ratio=bool_like,
        numeric_parse_ratio=num_r,
        timestamp_parse_ratio=ts_r,
    )
    duck_ty = _typeof_sample(conn, table, col_q)

    num_min = num_max = num_mean = num_median = num_std = num_var = None
    hist: List[Tuple[str, int]] = []
    out_n: Optional[int] = None
    out_pct: Optional[float] = None
    top_strings: List[Tuple[str, int]] = []

    if field.field_dtype == "M":
        num_min, num_max, num_mean, num_median, num_std, num_var = _numeric_block(conn, table, col_q)
        hist = _measure_range_distribution(conn, table, col_q, row_count, fine_bins=200, top_show=50)
        out_n, out_pct = _outlier_iqr(conn, table, col_q)
    elif field.field_dtype == "D":
        top_strings = _dimension_value_distribution(conn, table, col_q, row_count, top_n=50)

    return FieldSummaryReport(
        field_name=field.name,
        meta_field_type=field.field_type,
        field_dtype=field.field_dtype,
        inferred_semantic=inferred,
        duckdb_typeof_sample=duck_ty,
        row_count=row_count,
        null_count=null_count,
        empty_string_count=empty_string_count,
        missing_pct=missing_pct,
        distinct_count=distinct_count,
        unique_pct=unique_pct,
        is_high_cardinality=is_high_cardinality,
        numeric_min=num_min,
        numeric_max=num_max,
        numeric_mean=num_mean,
        numeric_median=num_median,
        numeric_stddev_pop=num_std,
        numeric_variance_pop=num_var,
        top_string_values=top_strings,
        histogram_bins=hist,
        outlier_count_iqr=out_n,
        outlier_pct=out_pct,
    )


def _duplicate_full_rows(conn: object, table: str, columns: Sequence[str]) -> DatasetDuplicateReport:
    """Count duplicate rows via ``GROUP BY`` all columns."""

    if not columns:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        n = int(row[0] or 0)
        return DatasetDuplicateReport(total_rows=n, duplicate_extra_rows=0, duplicate_pct=0.0, distinct_full_rows=n)

    grp = _sql_list_idents(columns)
    row = conn.execute(
        f"""
        WITH g AS (
          SELECT COUNT(*) AS cnt
          FROM {table}
          GROUP BY {grp}
        )
        SELECT
          (SELECT COUNT(*) FROM {table}) AS total,
          SUM(CASE WHEN cnt > 1 THEN cnt - 1 ELSE 0 END) AS dup_extra,
          COUNT(*) AS distinct_groups
        FROM g
        """
    ).fetchone()
    total = int(row[0] or 0)
    dup_extra = int(row[1] or 0)
    distinct_groups = int(row[2] or 0)
    pct = 100.0 * dup_extra / total if total else 0.0
    return DatasetDuplicateReport(
        total_rows=total,
        duplicate_extra_rows=dup_extra,
        duplicate_pct=pct,
        distinct_full_rows=distinct_groups,
    )


def compute_dataset_summary_report(
    database_path: str,
    table_name: str,
    meta: MetaDefinition,
) -> DatasetSummaryReport:
    """Build :class:`DatasetSummaryReport` for every field plus duplicate tracking.

    Purpose
    -------
    Main entry for the Summary tab worker: one DuckDB connection, sequential
    per-field queries (predictable memory; large tables still OK for OLAP).

    Internal Logic
    ---------------
    Connect, loop :meth:`_summarize_field`, then :meth:`_duplicate_full_rows` on
    all ``meta.fields`` names.

    Example invocation
    --------------------
    ``report = compute_dataset_summary_report(str(path), \"data\", meta)``
    """

    import duckdb  # type: ignore

    conn = duckdb.connect(database=database_path)
    try:
        fields = [_summarize_field(conn, table_name, f) for f in meta.fields]
        col_names = [f.name for f in meta.fields]
        dups = _duplicate_full_rows(conn, table_name, col_names)
        return DatasetSummaryReport(fields=fields, duplicates=dups)
    finally:
        conn.close()


def format_summary_plaintext(report: DatasetSummaryReport, max_chars: int = 12000) -> str:
    """Render a compact plain-text digest (optional export / tooltip).

    Purpose
    -------
    Provide a non-HTML fallback for copying summary content to the clipboard.

    Internal Logic
    ---------------
    Concatenate duplicate banner and per-field lines; truncate with ``…``.

    Example invocation
    --------------------
    ``txt = format_summary_plaintext(report)[:500]``
    """

    lines: List[str] = [
        f"Rows: {report.duplicates.total_rows}; duplicate extra rows: "
        f"{report.duplicates.duplicate_extra_rows} ({report.duplicates.duplicate_pct:.2f}%)",
        "",
    ]
    for fr in report.fields:
        lines.append(f"=== {fr.field_name} ({fr.inferred_semantic}, meta {fr.meta_field_type}) ===")
        lines.append(
            f"  missing%={fr.missing_pct:.2f} nulls={fr.null_count} empty_str={fr.empty_string_count} "
            f"distinct={fr.distinct_count} unique%={fr.unique_pct:.2f}"
        )
        if fr.numeric_mean is not None:
            lines.append(
                f"  min={fr.numeric_min} max={fr.numeric_max} mean={fr.numeric_mean} "
                f"median={fr.numeric_median} std={fr.numeric_stddev_pop} var={fr.numeric_variance_pop}"
            )
        if fr.outlier_count_iqr is not None:
            lines.append(f"  IQR outliers: {fr.outlier_count_iqr} ({fr.outlier_pct:.2f}%)")
        if fr.top_string_values:
            top = ", ".join(f"{k}:{v}" for k, v in fr.top_string_values[:5])
            lines.append(f"  top values: {top}")
        lines.append("")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text
