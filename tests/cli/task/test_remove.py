"""Tests for ``conda task remove``."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from conda_workspaces.cli.task.remove import execute_remove
from conda_workspaces.exceptions import TaskNotFoundError
from conda_workspaces.parsers.toml import CondaTomlParser


def _remove_args(
    file: Path, task_name: str, *, dry_run: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(
        file=file,
        task_name=task_name,
        dry_run=dry_run,
        quiet=False,
        verbose=0,
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
