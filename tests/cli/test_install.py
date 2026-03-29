"""Tests for conda_workspaces.cli.install."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.install import execute_install

if TYPE_CHECKING:
    from pathlib import Path

_INSTALL_DEFAULTS = {
    "file": None,
    "environment": None,
    "force_reinstall": False,
    "dry_run": False,
    "locked": False,
    "frozen": False,
}


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**{**_INSTALL_DEFAULTS, **kwargs})


def _stub_lockfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub generate_lockfile to a no-op for tests that don't care about it."""
    monkeypatch.setattr(
        "conda_workspaces.cli.install.generate_lockfile",
        lambda ctx, resolved_envs: None,
    )


def test_install_single_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_lockfile(monkeypatch)

    calls: list[tuple[str, bool, bool]] = []

    def fake_install(ctx, resolved, *, force_reinstall=False, dry_run=False):
        calls.append((resolved.name, force_reinstall, dry_run))

    monkeypatch.setattr(
        "conda_workspaces.cli.install.install_environment", fake_install
    )

    args = _make_args(environment="default")
    result = execute_install(args)
    assert result == 0
    assert len(calls) == 1
    assert calls[0][0] == "default"
    assert "Installing" in capsys.readouterr().out


def test_install_all_envs(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_lockfile(monkeypatch)

    calls: list[str] = []

    def fake_install(ctx, resolved, *, force_reinstall=False, dry_run=False):
        calls.append(resolved.name)

    monkeypatch.setattr(
        "conda_workspaces.cli.install.install_environment", fake_install
    )

    args = _make_args()
    result = execute_install(args)
    assert result == 0
    assert set(calls) == {"default", "test"}
    assert "2 environment(s)" in capsys.readouterr().out


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
        "conda_workspaces.cli.install.install_environment", fake_install
    )

    args = _make_args(environment="default", force_reinstall=force, dry_run=dry_run)
    execute_install(args)
    assert recorded[0] == (force, dry_run)


def test_install_generates_lockfile(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    monkeypatch.setattr(
        "conda_workspaces.cli.install.install_environment",
        lambda ctx, resolved, **kw: None,
    )

    lock_calls: list[dict] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.install.generate_lockfile",
        lambda ctx, resolved_envs: lock_calls.append(resolved_envs),
    )

    args = _make_args(environment="default")
    execute_install(args)
    assert len(lock_calls) == 1
    assert "default" in lock_calls[0]


def test_install_all_generates_lockfile(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    monkeypatch.setattr(
        "conda_workspaces.cli.install.install_environment",
        lambda ctx, resolved, **kw: None,
    )

    lock_calls: list[dict] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.install.generate_lockfile",
        lambda ctx, resolved_envs: lock_calls.append(resolved_envs),
    )

    args = _make_args()
    execute_install(args)
    assert len(lock_calls) == 1
    assert set(lock_calls[0].keys()) == {"default", "test"}


def test_install_dry_run_skips_lockfile(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    monkeypatch.setattr(
        "conda_workspaces.cli.install.install_environment",
        lambda ctx, resolved, **kw: None,
    )

    lock_calls: list[dict] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.install.generate_lockfile",
        lambda ctx, resolved_envs: lock_calls.append(resolved_envs),
    )

    args = _make_args(environment="default", dry_run=True)
    execute_install(args)
    assert lock_calls == []


def test_install_frozen_single(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)

    locked_calls: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.install.install_from_lockfile",
        lambda ctx, name: locked_calls.append(name),
    )

    args = _make_args(environment="default", frozen=True)
    result = execute_install(args)
    assert result == 0
    assert locked_calls == ["default"]
    assert "from lockfile" in capsys.readouterr().out


def test_install_frozen_all(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)

    locked_calls: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.install.install_from_lockfile",
        lambda ctx, name: locked_calls.append(name),
    )

    args = _make_args(frozen=True)
    result = execute_install(args)
    assert result == 0
    assert set(locked_calls) == {"default", "test"}
    assert "from lockfiles" in capsys.readouterr().out


def test_install_locked_validates_freshness(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--locked fails when lockfile is older than the manifest."""
    import time

    from conda_workspaces.exceptions import LockfileStaleError

    monkeypatch.chdir(pixi_workspace)

    lock_file = pixi_workspace / "conda.lock"
    lock_file.write_text("version: 1\n", encoding="utf-8")
    time.sleep(0.05)

    manifest = pixi_workspace / "pixi.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

    args = _make_args(locked=True)
    with pytest.raises(LockfileStaleError):
        execute_install(args)
