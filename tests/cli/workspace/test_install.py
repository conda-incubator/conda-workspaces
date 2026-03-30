"""Tests for conda_workspaces.cli.workspace.install."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.workspace.install import execute_install
from conda_workspaces.exceptions import LockfileStaleError

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULTS = {
    "file": None,
    "environment": None,
    "force_reinstall": False,
    "dry_run": False,
    "locked": False,
    "frozen": False,
}


def _stub_lockfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub generate_lockfile to a no-op for tests that don't care about it."""
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.generate_lockfile",
        lambda ctx, resolved_envs: None,
    )


@pytest.mark.parametrize(
    "env_arg, expected_names, output_fragment",
    [
        ("default", {"default"}, "Installed"),
        (None, {"default", "test"}, "Installed"),
    ],
    ids=["single-env", "all-envs"],
)
def test_install_envs(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env_arg: str | None,
    expected_names: set[str],
    output_fragment: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    calls: list[str] = []

    def fake_install(ctx, resolved, *, force_reinstall=False, dry_run=False):
        calls.append(resolved.name)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.install_environment", fake_install
    )

    lock_calls: list[dict] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.generate_lockfile",
        lambda ctx, resolved_envs: lock_calls.append(resolved_envs),
    )

    args = make_args(_DEFAULTS, environment=env_arg)
    result = execute_install(args)
    assert result == 0
    assert set(calls) == expected_names
    assert output_fragment in capsys.readouterr().out
    assert len(lock_calls) == 1
    assert set(lock_calls[0].keys()) == expected_names


@pytest.mark.parametrize(
    "force, dry_run",
    [
        (True, False),
        (False, True),
        (True, True),
    ],
    ids=["force", "dry-run", "force-dry-run"],
)
def test_install_flags_forwarded(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
    dry_run: bool,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_lockfile(monkeypatch)

    recorded: list[tuple[bool, bool]] = []

    def fake_install(ctx, resolved, *, force_reinstall=False, dry_run=False):
        recorded.append((force_reinstall, dry_run))

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.install_environment", fake_install
    )

    args = make_args(
        _DEFAULTS,
        environment="default",
        force_reinstall=force,
        dry_run=dry_run,
    )
    execute_install(args)
    assert recorded[0] == (force, dry_run)


def test_install_dry_run_skips_lockfile(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.install_environment",
        lambda ctx, resolved, **kw: None,
    )

    lock_calls: list[dict] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.generate_lockfile",
        lambda ctx, resolved_envs: lock_calls.append(resolved_envs),
    )

    args = make_args(_DEFAULTS, environment="default", dry_run=True)
    execute_install(args)
    assert lock_calls == []


@pytest.mark.parametrize(
    "env_arg, expected_names, output_fragment",
    [
        ("default", {"default"}, "Installed"),
        (None, {"default", "test"}, "Installed"),
    ],
    ids=["single-env", "all-envs"],
)
def test_install_frozen(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env_arg: str | None,
    expected_names: set[str],
    output_fragment: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    locked_calls: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.install_from_lockfile",
        lambda ctx, name: locked_calls.append(name),
    )

    args = make_args(_DEFAULTS, environment=env_arg, frozen=True)
    result = execute_install(args)
    assert result == 0
    assert set(locked_calls) == expected_names
    assert output_fragment in capsys.readouterr().out


def test_install_locked_validates_freshness(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--locked fails when lockfile is older than the manifest."""
    monkeypatch.chdir(pixi_workspace)

    lock_file = pixi_workspace / "conda.lock"
    lock_file.write_text("version: 1\n", encoding="utf-8")
    time.sleep(0.05)

    manifest = pixi_workspace / "pixi.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

    args = make_args(_DEFAULTS, locked=True)
    with pytest.raises(LockfileStaleError):
        execute_install(args)
