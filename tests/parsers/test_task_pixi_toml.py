"""Tests for task parsing in conda_workspaces.parsers.pixi_toml."""

from __future__ import annotations

import pytest

from conda_workspaces.exceptions import TaskParseError
from conda_workspaces.parsers.pixi_toml import PixiTomlParser


def test_can_handle(task_pixi_toml):
    parser = PixiTomlParser()
    assert parser.can_handle(task_pixi_toml)
    assert not parser.can_handle(task_pixi_toml.parent / "other.toml")


@pytest.mark.parametrize("task_name", ["build", "test", "lint"])
def test_parse_contains_task(task_pixi_toml, task_name):
    tasks = PixiTomlParser().parse_tasks(task_pixi_toml)
    assert task_name in tasks


@pytest.mark.parametrize(
    ("task_name", "attr", "expected"),
    [
        ("build", "cmd", "make build"),
        ("test", "cmd", "pytest"),
        ("lint", "description", "Lint the code"),
    ],
)
def test_parse_task_attr(task_pixi_toml, task_name, attr, expected):
    tasks = PixiTomlParser().parse_tasks(task_pixi_toml)
    assert getattr(tasks[task_name], attr) == expected


def test_parse_depends_on(task_pixi_toml):
    tasks = PixiTomlParser().parse_tasks(task_pixi_toml)
    assert tasks["test"].depends_on[0].task == "build"


def test_parse_platform_override(task_pixi_toml):
    tasks = PixiTomlParser().parse_tasks(task_pixi_toml)
    assert tasks["build"].platforms is not None
    assert "win-64" in tasks["build"].platforms
    assert tasks["build"].platforms["win-64"].cmd == "nmake build"


def test_parse_target_only_task(tmp_project):
    """A task defined only in [target.*.tasks] should still be created."""
    content = '[target.win-64.tasks]\nspecial = "win-only-cmd"\n'
    path = tmp_project / "pixi.toml"
    path.write_text(content)
    tasks = PixiTomlParser().parse_tasks(path)
    assert "special" in tasks
    assert tasks["special"].platforms is not None
    assert "win-64" in tasks["special"].platforms


def test_parse_invalid_toml(tmp_project):
    path = tmp_project / "pixi.toml"
    path.write_text("[broken\n")

    with pytest.raises(TaskParseError):
        PixiTomlParser().parse_tasks(path)
