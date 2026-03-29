"""Tests for conda_workspaces.cli.lock."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.lock import execute_lock
from conda_workspaces.exceptions import EnvironmentNotFoundError

if TYPE_CHECKING:
    from pathlib import Path

_LOCK_DEFAULTS = {
    "file": None,
    "environment": None,
}


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**{**_LOCK_DEFAULTS, **kwargs})


def test_lock_single_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)

    lock_calls: list[dict] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.lock.generate_lockfile",
        lambda ctx, resolved_envs: (
            lock_calls.append(resolved_envs),
            pixi_workspace / "conda.lock",
        )[1],
    )

    result = execute_lock(_make_args(environment="default"))
    assert result == 0
    assert len(lock_calls) == 1
    assert "default" in lock_calls[0]
    assert "Lockfile written to" in capsys.readouterr().out


def test_lock_unknown_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(EnvironmentNotFoundError):
        execute_lock(_make_args(environment="nonexistent"))


def test_lock_all_envs(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)

    lock_calls: list[dict] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.lock.generate_lockfile",
        lambda ctx, resolved_envs: (
            lock_calls.append(resolved_envs),
            pixi_workspace / "conda.lock",
        )[1],
    )

    result = execute_lock(_make_args())
    assert result == 0
    assert len(lock_calls) == 1
    assert set(lock_calls[0].keys()) == {"default", "test"}
    out = capsys.readouterr().out
    assert "2 environment(s) locked" in out
