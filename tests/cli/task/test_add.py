"""Tests for ``conda task add``."""

from __future__ import annotations

import argparse

import pytest

from conda_workspaces.cli.task.add import execute_add
from conda_workspaces.manifests.toml import CondaTomlParser


@pytest.mark.parametrize(
    ("dry_run", "expect_file_exists"),
    [
        (False, True),
        (True, False),
    ],
    ids=["real", "dry-run"],
)
def test_add_task(tmp_path, capsys, dry_run, expect_file_exists):
    path = tmp_path / "conda.toml"
    if not dry_run:
        path.write_text("[tasks]\n")

    args = argparse.Namespace(
        file=path,
        task_name="newtask",
        cmd="echo hello",
        depends_on=[],
        description="A new task" if not dry_run else None,
        dry_run=dry_run,
        quiet=False,
        verbosity=0,
        json=False,
    )
    result = execute_add(args)
    assert result == 0

    if expect_file_exists:
        tasks = CondaTomlParser().parse_tasks(path)
        assert "newtask" in tasks
    else:
        assert "Would add" in capsys.readouterr().out
        assert not path.exists()


def test_add_task_auto_detect_creates_default_file(tmp_path, monkeypatch, capsys):
    """When no file exists and none detected, defaults to conda.toml."""
    monkeypatch.chdir(tmp_path)

    args = argparse.Namespace(
        file=None,
        task_name="newtask",
        cmd="echo hi",
        depends_on=[],
        description=None,
        dry_run=False,
        quiet=False,
        verbosity=0,
        json=False,
    )
    result = execute_add(args)
    assert result == 0
    default_path = tmp_path / "conda.toml"
    assert default_path.exists()

    tasks = CondaTomlParser().parse_tasks(default_path)
    assert "newtask" in tasks
