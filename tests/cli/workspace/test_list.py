"""Tests for conda_workspaces.cli.workspace.list."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.workspace.list import execute_list
from conda_workspaces.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)

from ..conftest import make_args

if TYPE_CHECKING:
    from rich.console import Console

    from tests.conftest import CreateWorkspaceEnv

_DEFAULTS = {
    "file": None,
    "installed": False,
    "json": False,
    "envs": False,
    "environment": "default",
}


def test_list_all_environments(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, envs=True)
    result = execute_list(args, console=rich_console)
    assert result == 0
    out = rich_console.file.getvalue()
    assert "default" in out
    assert "test" in out


def test_list_installed_only(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, envs=True, installed=True)
    result = execute_list(args, console=rich_console)
    assert result == 0
    out = rich_console.file.getvalue()
    assert "No environments defined" in out


def test_list_installed_with_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    args = make_args(_DEFAULTS, envs=True, installed=True)
    execute_list(args, console=rich_console)
    out = rich_console.file.getvalue()
    assert "default" in out
    assert "test" not in out


def test_list_json_output(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, envs=True, json=True)
    execute_list(args, console=rich_console)
    out = rich_console.file.getvalue()
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
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(EnvironmentNotInstalledError):
        execute_list(make_args(_DEFAULTS))


def test_list_packages_undefined_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(EnvironmentNotFoundError):
        execute_list(make_args(_DEFAULTS, environment="nonexistent"))


@pytest.fixture
def _stub_prefix_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub PrefixData so _list_packages doesn't need real conda-meta."""

    @dataclass
    class FakeRecord:
        name: str
        version: str
        build: str

    records = [
        FakeRecord("numpy", "1.26.4", "py312h1234abc_0"),
        FakeRecord("python", "3.12.3", "h5678def_0"),
    ]

    class FakePrefixData:
        def __init__(self, prefix: str) -> None:
            self._prefix = prefix

        def is_environment(self) -> bool:
            return (Path(self._prefix) / "conda-meta").is_dir()

        def iter_records(self):
            return iter(records)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.list.PrefixData", FakePrefixData
    )


@pytest.mark.parametrize(
    "json_flag",
    [False, True],
    ids=["text", "json"],
)
def test_list_packages(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    tmp_workspace_env: CreateWorkspaceEnv,
    _stub_prefix_data: None,
    json_flag: bool,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    args = make_args(_DEFAULTS, json=json_flag)
    result = execute_list(args, console=rich_console)
    assert result == 0
    out = rich_console.file.getvalue()

    if json_flag:
        data = json.loads(out)
        names = {r["name"] for r in data}
        assert names == {"numpy", "python"}
        assert data[0]["version"] == "1.26.4"
    else:
        assert "numpy" in out
        assert "python" in out
        assert "1.26.4" in out


def test_list_packages_empty(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    class EmptyPrefixData:
        def __init__(self, prefix: str) -> None:
            self._prefix = prefix

        def is_environment(self) -> bool:
            return (Path(self._prefix) / "conda-meta").is_dir()

        def iter_records(self):
            return iter([])

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.list.PrefixData", EmptyPrefixData
    )

    args = make_args(_DEFAULTS)
    result = execute_list(args, console=rich_console)
    assert result == 0
    assert "No packages in" in rich_console.file.getvalue()
