"""Tests for Visualize tab dimension/measure source list population logic."""

from __future__ import annotations

from pathlib import Path

from file_analyzer.meta_parser import FieldMeta, MetaDefinition
from file_analyzer.ui.models import LoadedDatasetContext
from file_analyzer.ui.visualize_tab import _dim_meas_source_rows_for_context


def test_dim_meas_rows_include_all_fields_when_quick_stats_empty() -> None:
    """Purpose: D/M rows are built for every metadata field even without quick stats.

    Internal Logic
    ----------------
    Build a :class:`LoadedDatasetContext` with ``quick_stats={}`` and assert
    :func:`_dim_meas_source_rows_for_context` returns one dimension and one measure.

    Example invocation
    --------------------
    ``pytest tests/test_visualize_source_lists.py -q``
    """

    meta = MetaDefinition(
        file_key_columns=["A"],
        fields=[
            FieldMeta(
                name="A",
                field_type="CustomBlob",
                field_length=None,
                description="da",
                field_dtype="D",
            ),
            FieldMeta(
                name="B",
                field_type="Unknown",
                field_length=None,
                description="mb",
                field_dtype="M",
            ),
        ],
    )
    ctx = LoadedDatasetContext(
        meta=meta,
        database_path=Path("."),
        temp_dir=Path("."),
        quick_stats={},
        source_data_path=Path("x.csv"),
        source_delimiter=",",
    )
    dims, meas = _dim_meas_source_rows_for_context(ctx)
    assert len(dims) == 1 and len(meas) == 1
    assert dims[0][0] == "A"
    assert meas[0][0] == "B"
    assert "Quick stats unavailable" in dims[0][1]
    assert "Quick stats unavailable" in meas[0][1]
