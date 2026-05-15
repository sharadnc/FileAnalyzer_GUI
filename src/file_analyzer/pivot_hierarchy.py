"""Pivot hierarchy layout: leaf aggregation SQL and Excel-style subtotal rows.

Purpose
-------
Support the Pivot Data tab when users pick multiple row dimensions, optional
column dimensions, and one or more measures. DuckDB returns one grouped ``leaf``
row per combination of row and column dimension values; this module pivots those
leaves into wide numeric columns and inserts subtotal / grand-total rows similar
to Microsoft Excel’s compact pivot layout.

Internal Logic
---------------
- :func:`build_pivot_leaf_sql` emits ``SELECT … GROUP BY`` with one aggregate column
  per selected measure (same aggregate keyword for all measures).
- :func:`col_key_from_values` stringifies column-dimension cells (joined with ``|``).
- :func:`build_excel_style_pivot_table` walks row-dimension prefixes in sort order,
  emitting a **subtotal** row for each non-leaf prefix, then **detail** rows at full
  depth, and finally a **Grand Total** row. When **one** measure is selected, after
  each measure's pivoted keys a **GRAND TOTAL** column (row sum across keys) uses that
  exact header text; with **multiple** measures that column is omitted.

Example invocation
--------------------
``cols, cells, kinds, depths, expandable, _ = build_excel_style_pivot_table(...)``
"""

from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, Dict, List, Mapping, Sequence, Tuple

LeafAggRow = Tuple[object, ...]
AggMatrix = DefaultDict[Tuple[object, ...], DefaultDict[str, List[float]]]
PrefixSums = DefaultDict[Tuple[object, ...], DefaultDict[str, List[float]]]


def _sql_ident(name: str) -> str:
    """Return a DuckDB double-quoted identifier (same rules as the grid tab)."""

    return '"' + str(name).replace('"', '""') + '"'


def build_pivot_leaf_sql(
    *,
    table_name: str,
    base_where_sql: str,
    row_dims: Sequence[str],
    col_dims: Sequence[str],
    measures: Sequence[str],
    agg: str,
) -> str:
    """Compose ``SELECT … GROUP BY`` returning one row per leaf aggregation bucket.

    Purpose
    -------
    Provide the single DuckDB statement the UI worker executes before pivoting
    results in Python.

    Internal Logic
    ---------------
    - Build ``WHERE (predicate)`` when ``base_where_sql`` is non-empty.
    - ``SELECT`` lists every row dimension, every column dimension (cast to
      ``VARCHAR`` for stable keys with alias ``__c_<name>``), then one aggregate
      column per measure (alias ``__m_<name>``).
    - ``GROUP BY`` lists all row and column identifiers in order.
    - ``ORDER BY`` uses leading columns for deterministic hierarchy walks.

    Example invocation
    --------------------
    ``sql = build_pivot_leaf_sql(table_name=\"data\", base_where_sql=\"\", row_dims=[\"A\"], col_dims=[], measures=[\"M\"], agg=\"SUM\")``

    Args:
        table_name: Physical DuckDB table name (e.g. ``data``).
        base_where_sql: Predicate without a leading ``WHERE`` keyword.
        row_dims: Ordered row dimension field names (non-empty).
        col_dims: Optional column dimension names (may be empty).
        measures: Non-empty list of measure field names.
        agg: ``SUM``, ``AVG``, ``MIN``, ``MAX``, or ``COUNT``.

    Returns:
        Executable SQL text.

    Raises:
        ValueError: On invalid dimension/measure overlap or unsupported aggregate.
    """

    if not row_dims:
        raise ValueError("Pivot requires at least one row dimension.")
    if not measures:
        raise ValueError("Pivot requires at least one measure.")
    row_set = set(row_dims)
    col_set = set(col_dims)
    if row_set & col_set:
        raise ValueError("Row dimensions and column dimensions must be disjoint.")
    agg_u = agg.strip().upper()
    if agg_u not in {"SUM", "AVG", "MIN", "MAX", "COUNT"}:
        raise ValueError(f"Unsupported aggregate: {agg!r}")

    wh = f"WHERE ({base_where_sql})" if base_where_sql.strip() else ""
    parts_select: list[str] = []
    parts_group: list[str] = []
    for d in row_dims:
        q = _sql_ident(d)
        parts_select.append(q)
        parts_group.append(q)
    for d in col_dims:
        q = _sql_ident(d)
        alias = _sql_ident("__c_" + d)
        parts_select.append(f"CAST({q} AS VARCHAR) AS {alias}")
        parts_group.append(q)

    agg_parts: list[str] = []
    for m in measures:
        mq = _sql_ident(m)
        alias = _sql_ident("__m_" + m)
        if agg_u == "COUNT":
            agg_parts.append(f"CAST(COUNT(*) AS DOUBLE) AS {alias}")
        else:
            agg_parts.append(f"{agg_u}(TRY_CAST({mq} AS DOUBLE)) AS {alias}")

    select_sql = ", ".join(parts_select + agg_parts)
    group_sql = ", ".join(parts_group)
    n_group = len(row_dims) + len(col_dims)
    order_sql = ", ".join(str(i) for i in range(1, n_group + 1))
    return f"SELECT {select_sql} FROM {table_name} {wh} GROUP BY {group_sql} ORDER BY {order_sql}"


def col_key_from_values(col_vals: Sequence[object]) -> str:
    """Flatten column-dimension cells into a single pivot column key string."""

    return "|".join("" if v is None else str(v) for v in col_vals)


def _add_vec(dst: List[float], src: Sequence[float]) -> None:
    """In-place add ``src`` into ``dst`` (same length)."""

    for i, v in enumerate(src):
        dst[i] += float(v)


def _ingest_leaf_rows(
    *,
    leaf_rows: Sequence[LeafAggRow],
    n_row: int,
    n_col: int,
    n_meas: int,
) -> Tuple[AggMatrix, PrefixSums, List[str]]:
    """Split SQL tuples into leaf matrices and prefix rollups."""

    leaf_mat: AggMatrix = defaultdict(lambda: defaultdict(lambda: [0.0] * n_meas))
    prefix_sums: PrefixSums = defaultdict(lambda: defaultdict(lambda: [0.0] * n_meas))
    col_keys: set[str] = set()

    for tup in leaf_rows:
        if len(tup) < n_row + n_col + n_meas:
            raise ValueError("Leaf row width does not match row/col/measure counts.")
        row_t = tuple(tup[:n_row])
        col_t = tuple(tup[n_row : n_row + n_col])
        meas = [float(tup[n_row + n_col + j] or 0.0) for j in range(n_meas)]
        # Use the same key string that :func:`_pivot_wide_column_header` expects for
        # ``n_col == 0`` (``"Total"``). A prior bug used ``"__TOTAL__"`` here but then
        # replaced only the sorted column list with ``"Total"``, so lookups in
        # :func:`_numeric_block_full` missed every cell and showed zeros.
        ck = col_key_from_values(col_t) if n_col > 0 else "Total"
        col_keys.add(ck)
        leaf_mat[row_t][ck] = meas
        for plen in range(1, n_row + 1):
            prefix = tuple(row_t[:plen])
            _add_vec(prefix_sums[prefix][ck], meas)

    sorted_cols = sorted(col_keys)
    return leaf_mat, prefix_sums, sorted_cols


def _sorted_distinct_at_depth(
    all_rows: Sequence[Tuple[object, ...]],
    depth: int,
    prefix: Tuple[object, ...],
) -> List[object]:
    """Return sorted unique values for ``row[depth]`` among rows sharing ``prefix``."""

    out: set[object] = set()
    plen = len(prefix)
    for r in all_rows:
        if len(r) <= depth:
            continue
        if r[:plen] != prefix:
            continue
        out.add(r[depth])
    return sorted(out, key=lambda x: (str(type(x)), str(x)))


def _label_cells_for_prefix(prefix: Tuple[object, ...], n_row: int) -> List[object]:
    """Place prefix components in the first cells; remaining row columns blank."""

    cells: List[object] = []
    for i in range(n_row):
        cells.append(prefix[i] if i < len(prefix) else "")
    return cells


def _numeric_block_full(
    ck_map: Mapping[str, List[float]],
    sorted_cols: Sequence[str],
    n_meas: int,
    *,
    include_row_grand: bool,
) -> List[object]:
    """Measure-major values for each column key, optionally plus a row grand per measure.

    Purpose
    -------
    Append one numeric cell per ``(measure, column_key)`` in measure-major order.
    When ``include_row_grand`` is True (single measure only), also append that
    measure's sum across ``sorted_cols`` (the **GRAND TOTAL** column). With several
    measures, row-wise sums mix different units and are omitted.

    Internal Logic
    ----------------
    For each measure index, sum values across ``sorted_cols`` from ``ck_map``;
    append each value, then append ``row_sum`` only if ``include_row_grand``.

    Example invocation
    --------------------
    ``nums = _numeric_block_full(m, (\"a\", \"b\"), 1, include_row_grand=True)``
    """

    nums: List[float] = []
    for mi in range(n_meas):
        row_sum = 0.0
        for ck in sorted_cols:
            vec = ck_map.get(ck)
            v = float(vec[mi]) if vec is not None and mi < len(vec) else 0.0
            nums.append(v)
            row_sum += v
        if include_row_grand:
            nums.append(row_sum)
    return [float(x) for x in nums]


def _compute_pivot_expandable(depths: Sequence[int], kinds: Sequence[str]) -> List[bool]:
    """Mark subtotal rows that own at least one strictly deeper descendant before the next sibling.

    Purpose
    -------
    Drive ``+`` / ``-`` affordances: only rows with nested content should toggle.

    Internal Logic
    ---------------
    For each index ``i`` with ``kinds[i] == \"subtotal\"``, scan forward until
    ``grand`` or a row with ``depths[j] <= depths[i]``; if any intermediate row has
    depth greater than ``depths[i]``, the node is expandable.

    Example invocation
    --------------------
    ``flags = _compute_pivot_expandable([1, 2, 2, 0], [\"subtotal\", \"detail\", \"detail\", \"grand\"])``
    """

    n = len(depths)
    out = [False] * n
    for i in range(n):
        if kinds[i] != "subtotal":
            continue
        d_i = depths[i]
        j = i + 1
        while j < n and kinds[j] != "grand":
            if depths[j] > d_i:
                out[i] = True
                break
            if depths[j] <= d_i:
                break
            j += 1
    return out


def _pivot_wide_column_header(
    *,
    measure: str,
    col_key: str,
    col_dims: Sequence[str],
    n_col: int,
    measure_count: int,
) -> str:
    """Label one wide pivot data column for the results grid (not the per-measure grand column).

    Purpose
    -------
    When column dimensions exist (for example ``REGION``), users expect headers
    like ``REGION-01`` that name the column field first, instead of
    ``POPESTIMATE2020 [01]`` which repeats the measure for every region bucket.

    Internal Logic
    ---------------
    - If ``n_col == 0``, keep the legacy shape: bare ``measure`` for the single
      ``Total`` bucket, else ``measure [col_key]``.
    - If ``n_col >= 1``, split ``col_key`` on ``|`` (same encoding as
      :func:`col_key_from_values`). Build ``{dim}-{value}`` for one dimension, or
      join ``dim-value`` pairs with ``" - "`` when several column dimensions exist.
      If more than one measure is selected, append `` · {measure}`` so repeated
      region columns across measures stay unique.

    Example invocation
    --------------------
    ``_pivot_wide_column_header(measure=\"POP\", col_key=\"3\", col_dims=(\"REGION\",), n_col=1, measure_count=1)``
    returns ``\"REGION-3\"``.
    """

    if n_col == 0:
        suffix = "" if col_key == "Total" else f" [{col_key}]"
        return f"{measure}{suffix}"

    parts = col_key.split("|") if col_key else []
    if len(parts) != len(col_dims) or not col_dims:
        joined_dims = "|".join(col_dims)
        return f"{joined_dims}-{col_key}" if joined_dims else f"{measure} [{col_key}]"

    if len(col_dims) == 1:
        base = f"{col_dims[0]}-{parts[0]}"
    else:
        base = " - ".join(f"{d}-{p}" for d, p in zip(col_dims, parts))

    if measure_count > 1:
        return f"{base} · {measure}"
    return base


def build_excel_style_pivot_table(
    *,
    leaf_rows: Sequence[LeafAggRow],
    row_dims: Sequence[str],
    col_dims: Sequence[str],
    measures: Sequence[str],
    agg: str,
    sql_executed: str,
) -> Tuple[List[str], List[List[object]], List[str], List[int], List[bool], str]:
    """Turn grouped leaf rows into wide columns plus subtotal / grand-total rows.

    Purpose
    -------
    Match a compact pivot presentation: subtotal rows at each row-dimension prefix,
    detail rows at full depth, and a final **Grand Total** row. When exactly **one**
    measure is selected, add a **Grand Total** column per measure (row sum across
    pivot column keys, header ``GRAND TOTAL``). With **multiple** measures, those
    row totals are omitted because they do not compare like with like.

    Internal Logic
    ---------------
    1. Ingest leaves into ``leaf_mat`` and ``prefix_sums``.
    2. Build column headers: for each measure, each sorted column key (when column
       dimensions exist, name buckets ``{ColumnDim}-{value}`` instead of
       ``{Measure} [value]``). If ``len(measures) == 1``, append a ``GRAND TOTAL``
       header after that measure's keys; otherwise do not.
    3. Depth-first walk: for each child extending ``prefix``, if still above leaf
       depth emit a **subtotal** row for ``prefix+child`` then recurse; at leaf depth
       emit one **detail** row per distinct full row key matching that prefix.
    4. Append a **grand** row summing leaf contributions only (no double-count of
       subtotals).

    Args:
        leaf_rows: Rows returned from :func:`build_pivot_leaf_sql`.
        row_dims: Row field names.
        col_dims: Column field names (only used for leaf parsing width).
        measures: Measure field names.
        agg: Aggregate label echo.
        sql_executed: SQL text echoed to the UI for export.

    Returns:
        ``(column_names, display_rows, row_kinds, row_depths, pivot_expandable, sql_executed)``
        with ``row_kinds`` in ``{"detail","subtotal","grand"}``; ``row_depths`` is the
        hierarchy depth (``len(prefix)`` for subtotals, ``len(row_dims)`` for details,
        ``0`` for grand); ``pivot_expandable`` is ``True`` where a subtotal row has
        visible descendants in the flat list.
    """

    n_row = len(row_dims)
    n_col = len(col_dims)
    n_meas = len(measures)
    include_row_grand = n_meas <= 1
    leaf_mat, prefix_sums, sorted_cols = _ingest_leaf_rows(
        leaf_rows=leaf_rows, n_row=n_row, n_col=n_col, n_meas=n_meas
    )

    all_row_keys = sorted(leaf_mat.keys(), key=lambda t: tuple(str(x) for x in t))

    grand_col: DefaultDict[str, List[float]] = defaultdict(lambda: [0.0] * n_meas)
    for _rk, col_map in leaf_mat.items():
        for ck, vec in col_map.items():
            _add_vec(grand_col[ck], vec)

    col_headers: List[str] = []
    for m in measures:
        for ck in sorted_cols:
            col_headers.append(
                _pivot_wide_column_header(
                    measure=m,
                    col_key=str(ck),
                    col_dims=col_dims,
                    n_col=n_col,
                    measure_count=n_meas,
                )
            )
        if include_row_grand:
            col_headers.append("GRAND TOTAL")

    out_cols = list(row_dims) + col_headers
    out_rows: List[List[object]] = []
    kinds: List[str] = []
    depths: List[int] = []

    def append_row(cells: List[object], kind: str, depth: int) -> None:
        out_rows.append(cells)
        kinds.append(kind)
        depths.append(depth)

    def walk(prefix: Tuple[object, ...], depth: int) -> None:
        if depth >= n_row:
            return
        for child in _sorted_distinct_at_depth(all_row_keys, depth, prefix):
            new_prefix = prefix + (child,)
            if len(new_prefix) < n_row:
                ck_map_sub = {k: list(v) for k, v in prefix_sums[new_prefix].items()}
                append_row(
                    _label_cells_for_prefix(new_prefix, n_row)
                    + _numeric_block_full(
                        ck_map_sub, sorted_cols, n_meas, include_row_grand=include_row_grand
                    ),
                    "subtotal",
                    len(new_prefix),
                )
                walk(new_prefix, depth + 1)
            else:
                rk = new_prefix
                if rk not in leaf_mat:
                    continue
                detail_map = {k: list(v) for k, v in leaf_mat[rk].items()}
                append_row(
                    [rk[i] if i < len(rk) else "" for i in range(n_row)]
                    + _numeric_block_full(
                        detail_map, sorted_cols, n_meas, include_row_grand=include_row_grand
                    ),
                    "detail",
                    n_row,
                )

    if n_row == 0:
        raise ValueError("Row dimensions required.")

    walk((), 0)

    grand_map = {k: list(v) for k, v in grand_col.items()}
    g_labels = [""] * max(0, n_row - 1) + ["Grand Total"]
    if n_row == 1:
        g_labels = ["Grand Total"]
    append_row(
        g_labels
        + _numeric_block_full(
            grand_map, sorted_cols, n_meas, include_row_grand=include_row_grand
        ),
        "grand",
        0,
    )

    expandable = _compute_pivot_expandable(depths, kinds)
    return out_cols, out_rows, kinds, depths, expandable, sql_executed


def run_pivot_pipeline(
    *,
    leaf_rows: Sequence[LeafAggRow],
    row_dims: Sequence[str],
    col_dims: Sequence[str],
    measures: Sequence[str],
    agg: str,
    sql_executed: str,
) -> Tuple[List[str], List[List[object]], List[str], List[int], List[bool], str]:
    """Thin wrapper matching the worker’s Python pivot entry point."""

    return build_excel_style_pivot_table(
        leaf_rows=leaf_rows,
        row_dims=row_dims,
        col_dims=col_dims,
        measures=measures,
        agg=agg,
        sql_executed=sql_executed,
    )
