"""Tests for conda_workspaces.cli.shell."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.shell import execute_shell
from conda_workspaces.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)

if TYPE_CHECKING:
    from pathlib import Path

_SHELL_DEFAULTS = {"file": None, "environment": "default", "cmd": []}


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**{**_SHELL_DEFAULTS, **kwargs})


@pytest.mark.parametrize(
    "env_name, expected_path_part",
    [
        ("default", "default"),
        ("test", "test"),
    ],
    ids=["default-env", "named-env"],
)
def test_shell_spawns_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    expected_path_part: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    meta = pixi_workspace / ".conda" / "envs" / env_name / "conda-meta"
    meta.mkdir(parents=True)
    (meta / "history").write_text("", encoding="utf-8")

    spawn_calls: list[dict] = []

    def fake_spawn(*, prefix, command=None):
        spawn_calls.append({"prefix": prefix, "command": command})
        return 0

    monkeypatch.setattr("conda_spawn.main.spawn", fake_spawn)

    args = _make_args(environment=env_name)
    result = execute_shell(args)
    assert result == 0
    assert len(spawn_calls) == 1
    assert expected_path_part in str(spawn_calls[0]["prefix"])
    assert spawn_calls[0]["command"] is None


@pytest.mark.parametrize(
    "cmd_input, expected_command",
    [
        (["--", "python", "-c", "print(1)"], ["python", "-c", "print(1)"]),
        (["python"], ["python"]),
        (["--", "echo", "hello"], ["echo", "hello"]),
        ([], None),
    ],
    ids=["strips-dashdash", "no-dashdash", "dashdash-multi-arg", "empty-cmd"],
)
def test_shell_command_passthrough(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd_input: list[str],
    expected_command: list[str] | None,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    meta = pixi_workspace / ".conda" / "envs" / "default" / "conda-meta"
    meta.mkdir(parents=True)
    (meta / "history").write_text("", encoding="utf-8")

    spawn_calls: list[dict] = []

    def fake_spawn(*, prefix, command=None):
        spawn_calls.append({"prefix": prefix, "command": command})
        return 0

    monkeypatch.setattr("conda_spawn.main.spawn", fake_spawn)

    args = _make_args(cmd=cmd_input)
    result = execute_shell(args)
    assert result == 0
    assert spawn_calls[0]["command"] == expected_command


@pytest.mark.parametrize(
    "env_name, exc_type",
    [
        ("default", EnvironmentNotInstalledError),
        ("nonexistent", EnvironmentNotFoundError),
    ],
    ids=["not-installed", "unknown-env"],
)
def test_shell_error(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    exc_type: type,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = _make_args(environment=env_name)
    with pytest.raises(exc_type):
        execute_shell(args)


def test_shell_propagates_exit_code(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(pixi_workspace)
    meta = pixi_workspace / ".conda" / "envs" / "default" / "conda-meta"
    meta.mkdir(parents=True)
    (meta / "history").write_text("", encoding="utf-8")

    def fake_spawn(*, prefix, command=None):
        return 42

    monkeypatch.setattr("conda_spawn.main.spawn", fake_spawn)

    args = _make_args()
    assert execute_shell(args) == 42
