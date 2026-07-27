"""Tests for conda_workspaces.cli.workspace.activate."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.workspace.activate import execute_activate
from conda_workspaces.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import CreateWorkspaceEnv

_DEFAULTS = {"manifest_file": None, "environment": "default"}


def test_activate_prints_command(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    printed: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.activate.print_activate",
        lambda prefix: printed.append(prefix),
    )

    args = make_args(_DEFAULTS)
    result = execute_activate(args)
    assert result == 0
    assert len(printed) == 1
    assert "default" in printed[0]


@pytest.mark.parametrize(
    "env_name, exc_type",
    [
        ("default", EnvironmentNotInstalledError),
        ("nonexistent", EnvironmentNotFoundError),
    ],
    ids=["not-installed", "unknown-env"],
)
def test_activate_error(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch, env_name: str, exc_type: type
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, environment=env_name)
    with pytest.raises(exc_type):
        execute_activate(args)
