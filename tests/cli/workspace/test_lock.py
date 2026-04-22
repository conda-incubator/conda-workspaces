"""Tests for conda_workspaces.cli.workspace.lock."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.workspace.lock import execute_lock
from conda_workspaces.exceptions import EnvironmentNotFoundError, PlatformError

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULTS = {
    "file": None,
    "environment": None,
    "platform": None,
}


@pytest.fixture
def capture_generate_lockfile(monkeypatch: pytest.MonkeyPatch, pixi_workspace: Path):
    """Patch ``generate_lockfile`` and return a list of captured kwargs.

    Each call is recorded as a dict with ``resolved_envs`` (dict of
    ``ResolvedEnvironment``), ``platforms``, and ``progress`` so tests
    can assert the CLI forwards ``--platform`` and friends correctly.
    """
    calls: list[dict] = []

    def fake_generate(ctx, resolved_envs, *, platforms=None, progress=None):
        calls.append(
            {
                "resolved_envs": resolved_envs,
                "platforms": platforms,
                "progress": progress,
            }
        )
        return pixi_workspace / "conda.lock"

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.generate_lockfile", fake_generate
    )
    return calls


@pytest.mark.parametrize(
    "env_arg, expected_keys, output_fragment",
    [
        ("default", {"default"}, "Updated"),
        (None, {"default", "test"}, "Updated"),
    ],
    ids=["single-env", "all-envs"],
)
def test_lock_envs(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    capture_generate_lockfile: list[dict],
    env_arg: str | None,
    expected_keys: set[str],
    output_fragment: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    result = execute_lock(make_args(_DEFAULTS, environment=env_arg))
    assert result == 0
    assert len(capture_generate_lockfile) == 1
    assert set(capture_generate_lockfile[0]["resolved_envs"].keys()) == expected_keys
    assert capture_generate_lockfile[0]["platforms"] is None
    assert output_fragment in capsys.readouterr().out


def test_lock_unknown_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(EnvironmentNotFoundError):
        execute_lock(make_args(_DEFAULTS, environment="nonexistent"))


def test_lock_forwards_platform_flag(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    """Repeated ``--platform`` values reach ``generate_lockfile`` as a tuple."""
    monkeypatch.chdir(pixi_workspace)

    result = execute_lock(make_args(_DEFAULTS, platform=["linux-64", "osx-arm64"]))
    assert result == 0
    assert capture_generate_lockfile[0]["platforms"] == ("linux-64", "osx-arm64")


def test_lock_rejects_undeclared_platform(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``--platform`` value absent from the manifest raises ``PlatformError``."""
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(PlatformError, match="freebsd-64"):
        execute_lock(make_args(_DEFAULTS, platform=["freebsd-64"]))
