"""Tests for ``conda task add``."""

from __future__ import annotations

import argparse

import pytest

from conda_workspaces.cli.task.add import execute_add
from conda_workspaces.exceptions import WorkspaceParseError
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


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("conda.toml", '[tasks]\nkeep = "echo keep"\n'),
        ("pixi.toml", '[tasks]\nkeep = "echo keep"\n'),
        (
            "pyproject.toml",
            '[tool.conda.tasks]\nkeep = "echo keep"\n',
        ),
    ],
    ids=["conda", "pixi", "pyproject"],
)
@pytest.mark.parametrize("boundary", ["leaf", "parent"], ids=["leaf", "parent"])
def test_add_task_rejects_symlinked_manifest(
    tmp_path,
    filename,
    content,
    boundary,
):
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
    args = argparse.Namespace(
        file=manifest,
        task_name="newtask",
        cmd="echo new",
        depends_on=[],
        description=None,
        dry_run=False,
        quiet=False,
        verbosity=0,
        json=False,
    )

    with pytest.raises(
        (WorkspaceParseError, NotADirectoryError),
        match="symbolic link",
    ):
        execute_add(args)

    assert outside.read_bytes() == before
    assert linked_boundary.is_symlink()
