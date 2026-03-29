"""Tests for conda_workspaces.cli.clean."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.clean import execute_clean
from conda_workspaces.exceptions import EnvironmentNotFoundError

if TYPE_CHECKING:
    from pathlib import Path

_CLEAN_DEFAULTS = {"file": None, "environment": None}


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**{**_CLEAN_DEFAULTS, **kwargs})


def _install_fake_env(workspace: Path, name: str) -> Path:
    meta = workspace / ".conda" / "envs" / name / "conda-meta"
    meta.mkdir(parents=True)
    (meta / "history").write_text("", encoding="utf-8")
    return meta.parent


def _stub_confirm_and_unregister(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub confirm_yn (auto-yes) and unregister_env (no-op)."""
    monkeypatch.setattr("conda_workspaces.cli.clean.confirm_yn", lambda *a, **kw: None)
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)


def test_clean_single_environment(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_confirm_and_unregister(monkeypatch)
    prefix = _install_fake_env(pixi_workspace, "default")
    assert prefix.is_dir()

    args = _make_args(environment="default")
    result = execute_clean(args)
    assert result == 0
    assert not prefix.is_dir()
    assert "Removed" in capsys.readouterr().out


def test_clean_all_environments(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_confirm_and_unregister(monkeypatch)
    _install_fake_env(pixi_workspace, "default")
    _install_fake_env(pixi_workspace, "test")

    args = _make_args()
    result = execute_clean(args)
    assert result == 0
    assert "Removed 2" in capsys.readouterr().out


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
    args = _make_args(environment=env_arg)
    result = execute_clean(args)
    assert result == 0
    assert expected_msg in capsys.readouterr().out


def test_clean_prompt_abort(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conda.exceptions import CondaSystemExit

    monkeypatch.chdir(pixi_workspace)
    _install_fake_env(pixi_workspace, "default")

    def raise_abort(*args, **kwargs):
        raise CondaSystemExit()

    monkeypatch.setattr("conda_workspaces.cli.clean.confirm_yn", raise_abort)
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)

    args = _make_args(environment="default")
    result = execute_clean(args)
    assert result == 0
    # Env should still exist
    assert (pixi_workspace / ".conda" / "envs" / "default" / "conda-meta").is_dir()


def test_clean_all_prompt_abort(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conda.exceptions import CondaSystemExit

    monkeypatch.chdir(pixi_workspace)
    _install_fake_env(pixi_workspace, "default")

    def raise_abort(*args, **kwargs):
        raise CondaSystemExit()

    monkeypatch.setattr("conda_workspaces.cli.clean.confirm_yn", raise_abort)
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)

    args = _make_args()
    result = execute_clean(args)
    assert result == 0


def test_clean_undefined_environment(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clean with an undefined env raises EnvironmentNotFoundError."""
    monkeypatch.chdir(pixi_workspace)
    args = _make_args(environment="nonexistent")
    with pytest.raises(EnvironmentNotFoundError, match="not defined"):
        execute_clean(args)
