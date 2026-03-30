"""Tests for conda_workspaces.cli.workspace.clean."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conda.exceptions import CondaSystemExit

from conda_workspaces.cli.workspace.clean import execute_clean
from conda_workspaces.exceptions import EnvironmentNotFoundError

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import CreateWorkspaceEnv

_DEFAULTS = {"file": None, "environment": None}


def _stub_confirm_and_unregister(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub confirm_yn (auto-yes) and unregister_env (no-op)."""
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.clean.confirm_yn", lambda *a, **kw: None
    )
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)


def test_clean_single_environment(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_confirm_and_unregister(monkeypatch)
    prefix = tmp_workspace_env(pixi_workspace, "default")
    assert prefix.is_dir()

    args = make_args(_DEFAULTS, environment="default")
    result = execute_clean(args)
    assert result == 0
    assert not prefix.is_dir()
    assert "Removed" in capsys.readouterr().out


def test_clean_all_environments(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_confirm_and_unregister(monkeypatch)
    tmp_workspace_env(pixi_workspace, "default")
    tmp_workspace_env(pixi_workspace, "test")

    args = make_args(_DEFAULTS)
    result = execute_clean(args)
    assert result == 0
    assert "Removed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "env_arg, expected_msg",
    [
        ("default", "not installed"),
        (None, "No environments"),
    ],
    ids=["single-not-installed", "none-installed"],
)
def test_clean_nothing_to_remove(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env_arg: str | None,
    expected_msg: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, environment=env_arg)
    result = execute_clean(args)
    assert result == 0
    assert expected_msg in capsys.readouterr().out


@pytest.mark.parametrize(
    "env_arg",
    ["default", None],
    ids=["single-env", "all-envs"],
)
def test_clean_prompt_abort(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    env_arg: str | None,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    def raise_abort(*args, **kwargs):
        raise CondaSystemExit()

    monkeypatch.setattr("conda_workspaces.cli.workspace.clean.confirm_yn", raise_abort)
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)

    args = make_args(_DEFAULTS, environment=env_arg)
    result = execute_clean(args)
    assert result == 0
    assert (pixi_workspace / ".conda" / "envs" / "default" / "conda-meta").is_dir()


def test_clean_undefined_environment(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clean with an undefined env raises EnvironmentNotFoundError."""
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, environment="nonexistent")
    with pytest.raises(EnvironmentNotFoundError, match="not defined"):
        execute_clean(args)
