"""Parametrized add/remove tests for all task parsers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from conda_workspaces.exceptions import TaskNotFoundError
from conda_workspaces.models import Task
from conda_workspaces.parsers.pixi_toml import PixiTomlParser
from conda_workspaces.parsers.pyproject_toml import PyprojectTomlParser

if TYPE_CHECKING:
    from conda_workspaces.parsers.base import ManifestParser


@pytest.mark.parametrize(
    ("parser_cls", "fixture"),
    [
        pytest.param(PixiTomlParser, "task_pixi_toml", id="pixi"),
        pytest.param(PyprojectTomlParser, "task_pyproject", id="pyproject"),
    ],
)
def test_add_task(
    request: pytest.FixtureRequest,
    parser_cls: type[ManifestParser],
    fixture: str,
) -> None:
    path = request.getfixturevalue(fixture)
    parser = parser_cls()
    parser.add_task(path, "new", Task(name="new", cmd="echo new"))
    tasks = parser.parse_tasks(path)
    assert "new" in tasks
    assert tasks["new"].cmd == "echo new"


@pytest.mark.parametrize(
    ("parser_cls", "fixture"),
    [
        pytest.param(PixiTomlParser, "task_pixi_toml", id="pixi"),
        pytest.param(PyprojectTomlParser, "task_pyproject", id="pyproject"),
    ],
)
def test_remove_task(
    request: pytest.FixtureRequest,
    parser_cls: type[ManifestParser],
    fixture: str,
) -> None:
    path = request.getfixturevalue(fixture)
    parser = parser_cls()
    parser.remove_task(path, "build")
    tasks = parser.parse_tasks(path)
    assert "build" not in tasks


@pytest.mark.parametrize(
    ("parser_cls", "fixture"),
    [
        pytest.param(PixiTomlParser, "task_pixi_toml", id="pixi"),
        pytest.param(PyprojectTomlParser, "task_pyproject", id="pyproject"),
    ],
)
def test_remove_nonexistent(
    request: pytest.FixtureRequest,
    parser_cls: type[ManifestParser],
    fixture: str,
) -> None:
    path = request.getfixturevalue(fixture)
    with pytest.raises(TaskNotFoundError):
        parser_cls().remove_task(path, "nonexistent")
