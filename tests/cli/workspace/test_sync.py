"""Tests for conda_workspaces.cli.workspace.sync."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from conda_workspaces.cli.workspace.sync import (
    affected_environments,
    sync_environments,
)
from conda_workspaces.models import Environment, Feature, WorkspaceConfig

if TYPE_CHECKING:
    from pathlib import Path


def _config(**envs_spec: dict) -> WorkspaceConfig:
    """Build a minimal config with the given environments.

    *envs_spec* maps env name -> dict with optional ``features`` and
    ``no_default_feature`` keys.
    """
    config = WorkspaceConfig()
    for name, spec in envs_spec.items():
        features = spec.get("features", [])
        for fname in features:
            if fname not in config.features:
                config.features[fname] = Feature(name=fname)
        config.environments[name] = Environment(
            name=name,
            features=features,
            no_default_feature=spec.get("no_default_feature", False),
        )
    return config


@pytest.mark.parametrize(
    "envs, target, expected",
    [
        (
            {
                "default": {},
                "dev": {"features": ["dev"]},
                "docs": {"features": ["docs"], "no_default_feature": True},
            },
            None,
            {"default", "dev"},
        ),
        (
            {"default": {}, "dev": {"features": ["dev"]}},
            "default",
            {"default", "dev"},
        ),
        (
            {
                "default": {},
                "dev": {"features": ["dev"]},
                "test": {"features": ["test"]},
            },
            "dev",
            {"dev"},
        ),
        (
            {"default": {}},
            "does-not-exist",
            set(),
        ),
    ],
    ids=[
        "default-feature-skips-no-default",
        "explicit-default-name",
        "named-feature-matches-composers",
        "unknown-feature-empty",
    ],
)
def test_affected_environments(
    envs: dict, target: str | None, expected: set[str]
) -> None:
    """``affected_environments`` returns the envs whose composition is touched."""
    config = _config(**envs)
    assert set(affected_environments(config, target)) == expected


@pytest.fixture
def captured_console() -> Console:
    """A Console that writes to StringIO so we can inspect output."""
    return Console(file=StringIO(), width=200)


@pytest.fixture
def fake_ctx(tmp_path: Path):
    """A minimal ``WorkspaceContext`` stand-in using *tmp_path* as the env prefix."""

    class FakeCtx:
        platform = "linux-64"

        def env_prefix(self, name: str):
            return tmp_path

    return FakeCtx()


@pytest.fixture
def sync_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub resolve / install / lockfile helpers and record which ran."""
    calls: list[str] = []

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.resolve_environment",
        lambda config, name, platform: type("R", (), {"name": name})(),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        lambda *a, **k: calls.append("install"),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda *a, **k: calls.append("lock"),
    )
    return calls


def test_sync_no_env_names_is_noop(
    captured_console: Console,
    fake_ctx,
    sync_calls: list[str],
) -> None:
    """``sync_environments`` with an empty list does nothing."""
    sync_environments(
        _config(default={}),
        fake_ctx,
        [],
        console=captured_console,
    )
    assert sync_calls == []


@pytest.mark.parametrize(
    "flags, expected_calls",
    [
        ({}, ["install", "lock"]),
        ({"no_install": True}, ["lock"]),
        ({"dry_run": True}, ["install"]),
        ({"no_install": True, "dry_run": True}, []),
    ],
    ids=["default", "no-install", "dry-run", "no-install-and-dry-run"],
)
def test_sync_pipeline_respects_flags(
    captured_console: Console,
    fake_ctx,
    sync_calls: list[str],
    flags: dict,
    expected_calls: list[str],
) -> None:
    """Install and lockfile steps run only when their gating flags are false."""
    sync_environments(
        _config(default={}),
        fake_ctx,
        ["default"],
        console=captured_console,
        **flags,
    )
    assert sync_calls == expected_calls


@pytest.mark.parametrize(
    "spawn_env, hint_expected",
    [("1", True), (None, False)],
    ids=["inside-spawn", "outside-spawn"],
)
def test_sync_activate_d_hint_respects_conda_spawn(
    tmp_path: Path,
    captured_console: Console,
    monkeypatch: pytest.MonkeyPatch,
    fake_ctx,
    spawn_env: str | None,
    hint_expected: bool,
) -> None:
    """A new activate.d script only prints the re-spawn hint inside a spawned shell."""
    activate_d = tmp_path / "etc" / "conda" / "activate.d"

    def fake_install(ctx, resolved, *, force_reinstall=False, dry_run=False):
        activate_d.mkdir(parents=True, exist_ok=True)
        (activate_d / "pkg-activate.sh").write_text("# hook")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.resolve_environment",
        lambda config, name, platform: type("R", (), {"name": name})(),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda ctx, resolved_envs: None,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment", fake_install
    )
    if spawn_env is None:
        monkeypatch.delenv("CONDA_SPAWN", raising=False)
    else:
        monkeypatch.setenv("CONDA_SPAWN", spawn_env)

    sync_environments(
        _config(default={}),
        fake_ctx,
        ["default"],
        console=captured_console,
    )

    out = " ".join(captured_console.file.getvalue().split())
    assert ("new activation scripts" in out) is hint_expected
    if hint_expected:
        assert "conda workspace shell" in out
