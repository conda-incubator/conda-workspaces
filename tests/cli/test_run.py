"""Tests for conda_workspaces.cli.run."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from conda.exceptions import ArgumentError

import conda_workspaces.cli.run as run_mod
from conda_workspaces.cli.run import _try_run_task, execute_run
from conda_workspaces.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)

from .conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import CreateWorkspaceEnv

_DEFAULTS = {"file": None, "environment": "default", "cmd": []}


@dataclass
class FakeResponse:
    """Stand-in for subprocess_call return value."""

    rc: int = 0
    stdout: str = ""
    stderr: str = ""


def _stub_run_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rc: int = 0,
    recorded_cmds: list | None = None,
) -> None:
    """Stub all conda imports used by _run_command for success paths."""
    if recorded_cmds is None:
        recorded_cmds = []

    class FakeContext:
        root_prefix = "/fake/root"

    monkeypatch.setattr("conda_workspaces.cli.run.conda_context", FakeContext())

    def fake_wrap(root_prefix, prefix, dev, debug, cmd):
        recorded_cmds.append(cmd)
        return "/tmp/fake_script.sh", ["bash", "/tmp/fake_script.sh"]

    monkeypatch.setattr("conda_workspaces.cli.run.wrap_subprocess_call", fake_wrap)

    def fake_subprocess_call(
        command, *, env=None, path=None, raise_on_error=False, capture_output=False
    ):
        return FakeResponse(rc=rc)

    monkeypatch.setattr(
        "conda_workspaces.cli.run.subprocess_call", fake_subprocess_call
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.run.encode_environment", lambda env: env
    )
    monkeypatch.setattr("conda_workspaces.cli.run.rm_rf", lambda path: None)


def _block_task_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure task detection returns None so tests exercise the command path."""
    monkeypatch.setattr(run_mod, "_try_run_task", lambda args, name: None)


@pytest.mark.parametrize(
    "cmd, exc_type, match",
    [
        ([], ArgumentError, "No command"),
        (["echo", "hi"], EnvironmentNotInstalledError, "not installed"),
        (["echo", "hi"], EnvironmentNotFoundError, "not defined"),
    ],
    ids=["no-command", "not-installed", "undefined-env"],
)
def test_run_command_errors(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd: list[str],
    exc_type: type,
    match: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _block_task_detection(monkeypatch)
    overrides = {"cmd": cmd}
    if exc_type is EnvironmentNotFoundError:
        overrides["environment"] = "nonexistent"
    args = make_args(_DEFAULTS, **overrides)
    with pytest.raises(exc_type, match=match):
        execute_run(args)


def test_run_strips_double_dash(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    _block_task_detection(monkeypatch)
    recorded_cmds: list[list[str]] = []
    _stub_run_deps(monkeypatch, recorded_cmds=recorded_cmds)

    args = make_args(_DEFAULTS, cmd=["--", "pytest", "-v"])
    execute_run(args)

    assert recorded_cmds[0] == ["pytest", "-v"]


@pytest.mark.parametrize(
    "rc, cmd",
    [
        (0, ["echo", "hello"]),
        (42, ["false"]),
    ],
    ids=["success", "nonzero-exit"],
)
def test_run_exit_code(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    rc: int,
    cmd: list[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    _block_task_detection(monkeypatch)
    _stub_run_deps(monkeypatch, rc=rc)

    args = make_args(_DEFAULTS, cmd=cmd)
    result = execute_run(args)
    assert result == rc


@pytest.mark.parametrize(
    "cmd, expected_task",
    [
        (["mytask"], "mytask"),
        (["build", "--release"], "build"),
        (["--", "mytask", "arg1"], "mytask"),
    ],
    ids=["simple-task", "task-with-args", "double-dash-task"],
)
def test_run_delegates_to_task(
    monkeypatch: pytest.MonkeyPatch,
    cmd: list[str],
    expected_task: str,
) -> None:
    """execute_run delegates to _try_run_task when it returns an exit code."""
    calls: list[str] = []

    def fake_try(args, name):
        calls.append(name)
        return 0

    monkeypatch.setattr(run_mod, "_try_run_task", fake_try)

    args = make_args(_DEFAULTS, cmd=cmd)
    result = execute_run(args)
    assert result == 0
    assert calls == [expected_task]


def test_try_run_task_returns_none_without_conda_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_try_run_task returns None when conda-tasks is not importable."""
    real_import = builtins.__import__

    def block_conda_tasks(name, *a, **kw):
        if name.startswith("conda_tasks"):
            raise ImportError("no conda_tasks")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", block_conda_tasks)

    args = make_args(_DEFAULTS, cmd=["sometask"])
    assert _try_run_task(args, "sometask") is None
