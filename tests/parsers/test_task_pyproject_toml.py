"""Tests for task parsing in conda_workspaces.parsers.pyproject_toml."""

from __future__ import annotations

import pytest

from conda_workspaces.models import Task
from conda_workspaces.parsers.pyproject_toml import PyprojectTomlParser


def test_can_handle(task_pyproject):
    assert PyprojectTomlParser().has_tasks(task_pyproject)


def test_can_handle_no_tasks(tmp_project):
    path = tmp_project / "pyproject.toml"
    path.write_text('[project]\nname = "example"\n')
    assert not PyprojectTomlParser().has_tasks(path)


@pytest.mark.parametrize("task_name", ["build", "test"])
def test_parse_contains_task(task_pyproject, task_name):
    tasks = PyprojectTomlParser().parse_tasks(task_pyproject)
    assert task_name in tasks


@pytest.mark.parametrize(
    ("task_name", "attr", "expected"),
    [
        ("build", "cmd", "make build"),
    ],
)
def test_parse_task_attr(task_pyproject, task_name, attr, expected):
    tasks = PyprojectTomlParser().parse_tasks(task_pyproject)
    assert getattr(tasks[task_name], attr) == expected


def test_parse_depends(task_pyproject):
    tasks = PyprojectTomlParser().parse_tasks(task_pyproject)
    assert tasks["test"].depends_on[0].task == "build"


def test_platform_override(task_pyproject):
    tasks = PyprojectTomlParser().parse_tasks(task_pyproject)
    assert tasks["build"].platforms is not None
    assert "win-64" in tasks["build"].platforms


def test_pixi_fallback(tmp_project):
    path = tmp_project / "pyproject.toml"
    path.write_text(
        '[project]\nname = "example"\n\n[tool.pixi.tasks]\nbuild = "make"\n'
    )
    parser = PyprojectTomlParser()
    assert parser.has_tasks(path)
    tasks = parser.parse_tasks(path)
    assert "build" in tasks


def test_parse_target_only_task(tmp_project):
    content = (
        '[project]\nname = "x"\n\n'
        "[tool.conda.tasks]\n"
        'build = "make"\n\n'
        "[tool.conda.target.win-64.tasks]\n"
        'special = "win-cmd"\n'
    )
    path = tmp_project / "pyproject.toml"
    path.write_text(content)
    tasks = PyprojectTomlParser().parse_tasks(path)
    assert "special" in tasks
    assert tasks["special"].platforms is not None


def test_add_task_pixi_only(tmp_project):
    path = tmp_project / "pyproject.toml"
    path.write_text(
        '[project]\nname = "example"\n\n[tool.pixi.workspace]\n'
        'channels = ["conda-forge"]\nplatforms = ["linux-64"]\n'
    )
    parser = PyprojectTomlParser()
    parser.add_task(path, "build", Task(name="build", cmd="make"))
    tasks = parser.parse_tasks(path)
    assert tasks["build"].cmd == "make"
