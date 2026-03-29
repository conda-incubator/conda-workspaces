"""Tests for conda_workspaces.cli.list."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

from conda_workspaces.cli.list import execute_list

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_LIST_DEFAULTS = {
    "file": None,
    "installed": False,
    "json": False,
    "envs": False,
    "environment": "default",
}


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**{**_LIST_DEFAULTS, **kwargs})


def test_list_all_environments(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = _make_args(envs=True)
    result = execute_list(args)
    assert result == 0
    out = capsys.readouterr().out
    assert "default" in out
    assert "test" in out


def test_list_installed_only(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = _make_args(envs=True, installed=True)
    result = execute_list(args)
    assert result == 0
    out = capsys.readouterr().out
    assert "No environments found" in out


def test_list_installed_with_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    meta = pixi_workspace / ".conda" / "envs" / "default" / "conda-meta"
    meta.mkdir(parents=True)
    (meta / "history").write_text("", encoding="utf-8")

    args = _make_args(envs=True, installed=True)
    execute_list(args)
    out = capsys.readouterr().out
    assert "default" in out
    assert "test" not in out


def test_list_json_output(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = _make_args(envs=True, json=True)
    execute_list(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    names = {row["name"] for row in data}
    assert "default" in names
    assert "test" in names


def test_list_packages_not_installed(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default list (packages) raises when the env is not installed."""
    from conda_workspaces.exceptions import EnvironmentNotInstalledError

    monkeypatch.chdir(pixi_workspace)
    import pytest as _pt

    with _pt.raises(EnvironmentNotInstalledError):
        execute_list(_make_args())
