"""Tests for :mod:`file_analyzer.automated_profiling_report`."""

from __future__ import annotations

from file_analyzer.automated_profiling_report import human_file_size


def test_human_file_size_bytes() -> None:
    """Small files should report in bytes."""

    assert "500" in human_file_size(500)
    assert "bytes" in human_file_size(500).lower()


def test_human_file_size_kb() -> None:
    """Sizes above 1024 should use KB or larger."""

    text = human_file_size(10 * 1024)
    assert "KB" in text
