"""Tests for conda_workspaces.cli.workspace.run."""

from __future__ import annotations

import argparse

import pytest

from conda_workspaces.exceptions import (
    CondaWorkspacesError,
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)


def test_run_no_command_raises(pixi_workspace, monkeypatch, tmp_workspace_env):
    """Running without a command raises CondaWorkspacesError."""
    from conda_workspaces.cli.workspace.run import execute_run

    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    args = argparse.Namespace(
        file=None,
        environment="default",
        cmd=[],
    )
    with pytest.raises(CondaWorkspacesError, match="No command specified"):
        execute_run(args)


@pytest.mark.parametrize(
    "env_name, expected_exc",
    [
        ("nonexistent", EnvironmentNotFoundError),
        ("default", EnvironmentNotInstalledError),
    ],
    ids=["undefined-env", "not-installed"],
)
def test_run_env_error_raises(pixi_workspace, monkeypatch, env_name, expected_exc):
    """Running with an invalid environment raises the appropriate error."""
    from conda_workspaces.cli.workspace.run import execute_run

    monkeypatch.chdir(pixi_workspace)

    args = argparse.Namespace(
        file=None,
        environment=env_name,
        cmd=["echo", "hi"],
    )
    with pytest.raises(expected_exc):
        execute_run(args)


def test_run_strips_double_dash(pixi_workspace, monkeypatch, tmp_workspace_env):
    """The leading '--' separator is stripped from the command."""
    from conda_workspaces.cli.workspace.run import execute_run

    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    called_with = []

    def fake_conda_run(args, parser):
        called_with.append(args.executable_call)
        return 0

    monkeypatch.setattr(
        "conda.cli.main_run.execute", fake_conda_run,
    )

    args = argparse.Namespace(
        file=None,
        environment="default",
        cmd=["--", "echo", "hello"],
    )
    result = execute_run(args)
    assert result == 0
    assert called_with[0] == ["echo", "hello"]


def test_run_returns_nonzero_on_failure(pixi_workspace, monkeypatch, tmp_workspace_env):
    """Non-zero exit from conda run is propagated."""
    from conda_workspaces.cli.workspace.run import execute_run

    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    def fake_conda_run(args, parser):
        return 42

    monkeypatch.setattr(
        "conda.cli.main_run.execute", fake_conda_run,
    )

    args = argparse.Namespace(
        file=None,
        environment="default",
        cmd=["false"],
    )
    result = execute_run(args)
    assert result == 42
