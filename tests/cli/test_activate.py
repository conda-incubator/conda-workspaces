"""Tests for conda_workspaces.cli.activate."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.activate import execute_activate
from conda_workspaces.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)

if TYPE_CHECKING:
    from pathlib import Path

_ACTIVATE_DEFAULTS = {"file": None, "environment": "default"}


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**{**_ACTIVATE_DEFAULTS, **kwargs})


def test_activate_prints_command(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(pixi_workspace)
    # Fake-install the default env
    meta = pixi_workspace / ".conda" / "envs" / "default" / "conda-meta"
    meta.mkdir(parents=True)
    (meta / "history").write_text("", encoding="utf-8")

    printed: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.activate.print_activate",
        lambda prefix: printed.append(prefix),
    )

    args = _make_args()
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
    args = _make_args(environment=env_name)
    with pytest.raises(exc_type):
        execute_activate(args)
