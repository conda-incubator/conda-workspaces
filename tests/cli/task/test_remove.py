"""Tests for ``conda task remove``."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from conda_workspaces.cli.task.remove import execute_remove
from conda_workspaces.exceptions import TaskNotFoundError, WorkspaceParseError
from conda_workspaces.manifests.toml import CondaTomlParser


def _remove_args(
    file: Path, task_name: str, *, dry_run: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(
        file=file,
        task_name=task_name,
        dry_run=dry_run,
        quiet=False,
        verbosity=0,
        json=False,
    )


def test_remove_task(sample_yaml):
    result = execute_remove(_remove_args(sample_yaml, "lint"))
    assert result == 0

    tasks = CondaTomlParser().parse_tasks(sample_yaml)
    assert "lint" not in tasks


def test_remove_nonexistent(sample_yaml):
    with pytest.raises(TaskNotFoundError):
        execute_remove(_remove_args(sample_yaml, "nonexistent"))


def test_remove_dry_run(sample_yaml, capsys):
    result = execute_remove(_remove_args(sample_yaml, "lint", dry_run=True))
    assert result == 0
    assert "Would remove" in capsys.readouterr().out

    tasks = CondaTomlParser().parse_tasks(sample_yaml)
    assert "lint" in tasks


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("conda.toml", '[tasks]\nlint = "ruff check"\n'),
        ("pixi.toml", '[tasks]\nlint = "ruff check"\n'),
        (
            "pyproject.toml",
            '[tool.conda.tasks]\nlint = "ruff check"\n',
        ),
    ],
    ids=["conda", "pixi", "pyproject"],
)
@pytest.mark.parametrize("boundary", ["leaf", "parent"], ids=["leaf", "parent"])
def test_remove_task_rejects_symlinked_manifest(
    tmp_path: Path,
    filename: str,
    content: str,
    boundary: str,
) -> None:
    outside = tmp_path / "outside" / filename
    outside.parent.mkdir()
    outside.write_text(content, encoding="utf-8")
    if boundary == "leaf":
        linked_boundary = tmp_path / filename
        linked_boundary.symlink_to(outside)
        manifest = linked_boundary
    else:
        linked_boundary = tmp_path / "linked-parent"
        linked_boundary.symlink_to(outside.parent, target_is_directory=True)
        manifest = linked_boundary / filename
    before = outside.read_bytes()

    with pytest.raises(
        (WorkspaceParseError, NotADirectoryError),
        match="symbolic link",
    ):
        execute_remove(_remove_args(manifest, "lint"))

    assert outside.read_bytes() == before
    assert linked_boundary.is_symlink()
