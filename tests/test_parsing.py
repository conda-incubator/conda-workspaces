"""Tests for conda_workspaces.parsing."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.parsing import (
    document_values_equal,
    read_limited_text,
    validate_document_limits,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
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


_SHARED_DOCUMENT_VALUE = {"nested": ["value"]}


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        pytest.param(
            {"items": [{"value": "left"}]},
            {"items": [{"value": "right"}]},
            False,
            id="different-nested-leaf",
        ),
        pytest.param(
            [_SHARED_DOCUMENT_VALUE, _SHARED_DOCUMENT_VALUE],
            [{"nested": ["value"]}, {"nested": ["value"]}],
            True,
            id="different-alias-layout",
        ),
        pytest.param(
            {"left": 1},
            {"right": 1},
            False,
            id="mapping-key-mismatch",
        ),
        pytest.param(
            ["first", "second"],
            ["second", "first"],
            False,
            id="list-order-mismatch",
        ),
    ],
)
def test_document_values_equal(left: object, right: object, expected: bool) -> None:
    assert document_values_equal(left, right) is expected


def test_document_values_equal_does_not_reuse_failed_comparison() -> None:
    equal_container_pairs: dict[tuple[int, int], tuple[object, object]] = {}
    left = {"nested": ["left"]}
    right = {"nested": ["right"]}

    assert not document_values_equal(
        left,
        right,
        equal_container_pairs=equal_container_pairs,
    )
    assert not document_values_equal(
        left,
        right,
        equal_container_pairs=equal_container_pairs,
    )


def test_document_values_equal_does_not_iterate_shared_comparisons() -> None:
    class NonIterableMemo(dict[tuple[int, int], tuple[object, object]]):
        def __iter__(self) -> Iterator[tuple[int, int]]:
            raise AssertionError("Iterated the shared comparison memo")

    equal_container_pairs = NonIterableMemo()
    shared_left = {"nested": ["value"]}
    shared_right = {"nested": ["value"]}

    assert document_values_equal(
        shared_left,
        shared_right,
        equal_container_pairs=equal_container_pairs,
    )
    assert (id(shared_left), id(shared_right)) not in equal_container_pairs
    assert document_values_equal(
        {"wrapper": shared_left},
        {"wrapper": shared_right},
        equal_container_pairs=equal_container_pairs,
    )
