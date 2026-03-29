"""Tests for conda_workspaces.cli.info."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.info import execute_info

if TYPE_CHECKING:
    from pathlib import Path

_INFO_DEFAULTS = {"file": None, "environment": None, "json": False}


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**{**_INFO_DEFAULTS, **kwargs})


def test_info_workspace_overview(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = _make_args()
    result = execute_info(args)
    assert result == 0
    out = capsys.readouterr().out
    assert "Manifest:" in out
    assert "Environments:" in out
    assert "default" in out
    assert "test" in out
    assert "conda-forge" in out


@pytest.mark.parametrize(
    "env_name, expected_fragments",
    [
        (
            "default",
            ["Environment: default", "Installed:   no", "conda-forge", "python"],
        ),
        (
            "test",
            ["Environment: test", "python", "pytest", "Solve group: default"],
        ),
    ],
    ids=["default-text", "named-env"],
)
def test_info_env_details(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env_name: str,
    expected_fragments: list[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = _make_args(environment=env_name)
    result = execute_info(args)
    assert result == 0
    out = capsys.readouterr().out
    for fragment in expected_fragments:
        assert fragment in out


def test_info_installed_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    meta = pixi_workspace / ".conda" / "envs" / "default" / "conda-meta"
    meta.mkdir(parents=True)
    (meta / "history").write_text("", encoding="utf-8")
    for i in range(3):
        (meta / f"pkg-{i}.json").write_text("{}", encoding="utf-8")

    args = _make_args(environment="default")
    execute_info(args)
    out = capsys.readouterr().out
    assert "Installed:   yes" in out
    assert "Packages:    3" in out


def test_info_json_workspace(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = _make_args(json=True)
    execute_info(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["name"] == "cli-test"
    assert "environments" in data
    assert "channels" in data


def test_info_json_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = _make_args(environment="default", json=True)
    execute_info(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["name"] == "default"
    assert "conda_dependencies" in data
    assert "channels" in data


def test_info_shows_pypi_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PyPI dependencies appear in text output."""
    content = """\
[workspace]
name = "pypi-info"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"

[pypi-dependencies]
requests = ">=2.28"
"""
    (tmp_path / "pixi.toml").write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = _make_args(environment="default")
    execute_info(args)
    out = capsys.readouterr().out
    assert "PyPI dependencies:" in out
    assert "requests" in out
