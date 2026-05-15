"""Tests for :mod:`file_analyzer.summary_clipboard_html`."""

from __future__ import annotations

from pathlib import Path

from file_analyzer.meta_parser import MetaDefinition
from file_analyzer.summary_clipboard_html import build_summary_clipboard_html_document
from file_analyzer.summary_reports import DatasetDuplicateReport, DatasetSummaryReport
from file_analyzer.ui.models import LoadedDatasetContext


def test_clipboard_html_includes_profiling_and_shell() -> None:
    """Purpose: exported HTML should be a document with profiling and dataset banner.

    Internal Logic
    ----------------
    Build a minimal empty-field report and context, then assert key fragments exist.

    Example invocation
    ------------------
    ``pytest tests/test_summary_clipboard_html.py -q``
    """

    meta = MetaDefinition(file_key_columns=[], fields=[])
    ctx = LoadedDatasetContext(
        meta=meta,
        database_path=Path("_missing_clipboard_test.duckdb"),
        temp_dir=Path("."),
        quick_stats={},
        source_data_path=Path("unit_export.csv"),
        source_delimiter=",",
    )
    rep = DatasetSummaryReport(
        fields=[],
        duplicates=DatasetDuplicateReport(
            total_rows=0,
            duplicate_extra_rows=0,
            duplicate_pct=0.0,
            distinct_full_rows=0,
        ),
    )
    html_doc = build_summary_clipboard_html_document(ctx, rep)
    assert "<!DOCTYPE html>" in html_doc
    assert "Summary export" in html_doc
    assert "Executive Summary" in html_doc
    assert "Dataset" in html_doc
