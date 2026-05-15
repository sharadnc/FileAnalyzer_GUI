"""Tests for Data Grid ``ORDER BY`` SQL composition."""

from __future__ import annotations

from file_analyzer.ui.grid_tab import _build_grid_order_by_sql


def test_order_by_pk_only_when_no_user_sort() -> None:
    """Without user keys, terms are primary keys in order, quoted."""

    sql = _build_grid_order_by_sql([], ["SUMLEV", "STATE"])
    assert sql == '"SUMLEV" ASC, "STATE" ASC'


def test_user_sort_before_remaining_pk_tie_break() -> None:
    """User sort columns precede PK columns that are not yet in the clause."""

    sql = _build_grid_order_by_sql([("NAME", True)], ["SUMLEV", "STATE"])
    assert '"NAME" ASC' in sql
    assert sql.index('"NAME"') < sql.index('"SUMLEV"')
    assert '"SUMLEV" ASC' in sql
    assert '"STATE" ASC' in sql


def test_user_sort_on_pk_skips_duplicate_pk_append() -> None:
    """If the user already sorts by a PK column, do not repeat it from file keys."""

    sql = _build_grid_order_by_sql([("STATE", False)], ["SUMLEV", "STATE"])
    assert sql.count('"STATE"') == 1
    assert '"SUMLEV" ASC' in sql


def test_fallback_constant_when_no_columns() -> None:
    """DuckDB still receives a valid ORDER BY body when metadata is empty."""

    assert _build_grid_order_by_sql([], []) == "1"
