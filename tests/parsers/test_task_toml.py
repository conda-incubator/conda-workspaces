"""Tests for task parsing in conda_workspaces.parsers.toml."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from conda_workspaces.exceptions import TaskNotFoundError, TaskParseError
from conda_workspaces.models import Task, TaskArg, TaskDependency, TaskOverride
from conda_workspaces.parsers.toml import CondaTomlParser, tasks_to_toml

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sample_toml(tmp_project: Path) -> Path:
    """Create a sample conda.toml for testing."""
    content = """\
[tasks]
lint = "ruff check ."
_setup = "mkdir -p build/"
platform-task = "rm -rf build/"

[tasks.build]
cmd = "make build"
depends-on = ["configure"]
description = "Build the project"
inputs = ["src/**/*.py"]
outputs = ["dist/"]

[tasks.configure]
cmd = "cmake -G Ninja -S . -B .build"
description = "Configure build system"

[tasks.test]
cmd = "pytest"
env = { PYTHONPATH = "src" }
clean-env = true
args = [{ arg = "test_path", default = "tests/" }]

[tasks.check]
depends-on = ["test", "lint"]
description = "Run all checks"

[target.win-64.tasks]
platform-task = "rd /s /q build"
"""
    path = tmp_project / "conda.toml"
    path.write_text(content)
    return path


def test_can_handle(sample_toml):
    parser = CondaTomlParser()
    assert parser.can_handle(sample_toml)
    assert not parser.can_handle(sample_toml.parent / "pixi.toml")
    assert not parser.can_handle(sample_toml.parent / "other.toml")


@pytest.mark.parametrize(
    "task_name",
    ["build", "configure", "test", "lint", "check", "_setup", "platform-task"],
)
def test_parse_contains_task(sample_toml, task_name):
    tasks = CondaTomlParser().parse_tasks(sample_toml)
    assert task_name in tasks


@pytest.mark.parametrize(
    ("task_name", "attr", "expected"),
    [
        ("lint", "cmd", "ruff check ."),
        ("build", "inputs", ["src/**/*.py"]),
        ("build", "outputs", ["dist/"]),
        ("test", "clean_env", True),
        ("test", "env", {"PYTHONPATH": "src"}),
    ],
)
def test_parse_task_attr(sample_toml, task_name, attr, expected):
    tasks = CondaTomlParser().parse_tasks(sample_toml)
    assert getattr(tasks[task_name], attr) == expected


def test_parse_depends_on(sample_toml):
    tasks = CondaTomlParser().parse_tasks(sample_toml)
    assert len(tasks["build"].depends_on) == 1
    assert tasks["build"].depends_on[0].task == "configure"


def test_parse_alias(sample_toml):
    tasks = CondaTomlParser().parse_tasks(sample_toml)
    assert tasks["check"].is_alias
    assert tasks["check"].cmd is None


def test_parse_args(sample_toml):
    tasks = CondaTomlParser().parse_tasks(sample_toml)
    test = tasks["test"]
    assert len(test.args) == 1
    assert test.args[0].name == "test_path"
    assert test.args[0].default == "tests/"


def test_parse_hidden(sample_toml):
    tasks = CondaTomlParser().parse_tasks(sample_toml)
    assert tasks["_setup"].is_hidden


def test_parse_platform_override(sample_toml):
    tasks = CondaTomlParser().parse_tasks(sample_toml)
    pt = tasks["platform-task"]
    assert pt.platforms is not None
    assert "win-64" in pt.platforms
    assert pt.platforms["win-64"].cmd == "rd /s /q build"


def test_add_task(tmp_project):
    path = tmp_project / "conda.toml"
    parser = CondaTomlParser()
    task = Task(name="new", cmd="echo new")
    parser.add_task(path, "new", task)

    tasks = parser.parse_tasks(path)
    assert "new" in tasks
    assert tasks["new"].cmd == "echo new"


def test_add_task_to_existing(sample_toml):
    parser = CondaTomlParser()
    task = Task(name="extra", cmd="echo extra")
    parser.add_task(sample_toml, "extra", task)

    tasks = parser.parse_tasks(sample_toml)
    assert "extra" in tasks
    assert "build" in tasks


def test_remove_task(sample_toml):
    parser = CondaTomlParser()
    parser.remove_task(sample_toml, "lint")
    tasks = parser.parse_tasks(sample_toml)
    assert "lint" not in tasks


def test_remove_nonexistent(sample_toml):
    with pytest.raises(TaskNotFoundError):
        CondaTomlParser().remove_task(sample_toml, "nonexistent")


def test_parse_invalid(tmp_project):
    path = tmp_project / "conda.toml"
    path.write_text("[tasks\nbroken = ")
    with pytest.raises(TaskParseError):
        CondaTomlParser().parse_tasks(path)


def test_to_toml_inline_simple_cmd():
    task = Task(name="build", cmd="make")
    assert CondaTomlParser().task_to_toml_inline(task) == "make"


def test_to_toml_inline_with_fields():
    task = Task(
        name="test",
        cmd="pytest",
        depends_on=[TaskDependency(task="build")],
        description="Run tests",
        env={"PYTHONPATH": "src"},
        clean_env=True,
        inputs=["src/**/*.py"],
        outputs=["results/"],
    )
    result = CondaTomlParser().task_to_toml_inline(task)
    assert not isinstance(result, str)
    d = dict(result)
    assert d["cmd"] == "pytest"
    assert list(d["depends-on"]) == ["build"]  # ty: ignore[invalid-argument-type]
    assert d["description"] == "Run tests"
    assert d["clean-env"] is True


def test_to_toml_inline_with_args():
    task = Task(
        name="test",
        cmd="pytest {{ path }}",
        args=[TaskArg(name="path", default="tests/")],
    )
    result = CondaTomlParser().task_to_toml_inline(task)
    assert not isinstance(result, str)
    d = dict(result)
    assert d["args"][0] == {"arg": "path", "default": "tests/"}  # ty: ignore[not-subscriptable]


def test_tasks_to_toml_roundtrip(tmp_path):
    """Serializing then parsing should preserve tasks."""
    tasks = {
        "build": Task(name="build", cmd="make"),
        "test": Task(
            name="test",
            cmd="pytest",
            depends_on=[TaskDependency(task="build")],
        ),
    }
    toml_text = tasks_to_toml(tasks)
    out = tmp_path / "conda.toml"
    out.write_text(toml_text)
    parsed = CondaTomlParser().parse_tasks(out)
    assert set(parsed.keys()) == {"build", "test"}
    assert parsed["build"].cmd == "make"
    assert parsed["test"].depends_on[0].task == "build"


def test_tasks_to_toml_with_platforms(tmp_path):
    """Platform overrides should serialize to [target.<platform>.tasks] tables."""
    tasks = {
        "clean": Task(
            name="clean",
            cmd="rm -rf build/",
            platforms={
                "win-64": TaskOverride(cmd="rd /s /q build"),
            },
        ),
    }
    toml_text = tasks_to_toml(tasks)
    assert "[target.win-64.tasks]" in toml_text

    out = tmp_path / "conda.toml"
    out.write_text(toml_text)
    parsed = CondaTomlParser().parse_tasks(out)
    assert parsed["clean"].platforms is not None
    assert "win-64" in parsed["clean"].platforms
    assert parsed["clean"].platforms["win-64"].cmd == "rd /s /q build"
