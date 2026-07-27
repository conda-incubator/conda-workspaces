"""Tests for conda_workspaces.parsing."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.parsing import read_limited_text, validate_document_limits

if TYPE_CHECKING:
    from pathlib import Path


def test_read_limited_text_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable")
    path = tmp_path / "input.pipe"
    os.mkfifo(path)

    with pytest.raises(ValueError, match="not a regular file"):
        read_limited_text(path, maximum_bytes=4, label="test input")


def test_read_limited_text_keeps_read_only_symlink_compatibility(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("data", encoding="utf-8")
    path = tmp_path / "input.txt"
    path.symlink_to(target)

    assert read_limited_text(path, maximum_bytes=4, label="test input") == "data"


def test_validate_document_limits_returns_collection_item_count() -> None:
    value = {"items": [1, {"nested": [2]}]}

    assert validate_document_limits(value, label="test document") == 5
