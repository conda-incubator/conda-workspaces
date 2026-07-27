"""Tests for conda_workspaces.terminal."""

from __future__ import annotations

import pytest

from conda_workspaces.terminal import escape_for_console


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("erase\x1b[2J", r"erase\x1b[2J"),
        ("line\nnext\ttab", r"line\x0anext\x09tab"),
        ("c1\x9b2J", r"c1\x9b2J"),
        ("[bold]markup[/bold]", r"\[bold]markup\[/bold]"),
    ],
    ids=["escape", "line-controls", "c1", "rich-markup"],
)
def test_escape_for_console(value: str, expected: str) -> None:
    assert escape_for_console(value) == expected
