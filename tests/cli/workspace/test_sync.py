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
from conda_workspaces.exceptions import EnvironmentNotFoundError, PlatformError
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
        "conda_workspaces.cli.workspace.sync.install_environment",
        lambda *a, **k: calls.append("install"),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda *a, **k: calls.append("lock-dry" if k.get("dry_run") else "lock"),
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


def test_sync_rejects_unknown_environment(
    captured_console: Console,
    fake_ctx,
) -> None:
    with pytest.raises(EnvironmentNotFoundError):
        sync_environments(
            _config(default={}),
            fake_ctx,
            ["missing"],
            console=captured_console,
        )


@pytest.mark.parametrize(
    "flags, expected_calls",
    [
        ({}, ["install", "lock"]),
        ({"no_install": True}, ["lock"]),
        ({"dry_run": True}, ["install", "lock-dry"]),
        ({"no_install": True, "dry_run": True}, ["lock-dry"]),
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
    """Install respects its gate while lock generation always validates."""
    sync_environments(
        _config(default={}),
        fake_ctx,
        ["default"],
        console=captured_console,
        **flags,
    )
    assert sync_calls == expected_calls


@pytest.mark.parametrize(
    ("selected_name", "no_install", "expected_installed"),
    [
        ("default", False, ["default"]),
        ("windows", True, []),
    ],
    ids=["unselected-feature-platform", "no-install-feature-platform"],
)
def test_sync_locks_feature_only_platforms_without_host_validation(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    selected_name: str,
    no_install: bool,
    expected_installed: list[str],
) -> None:
    config = _config(default={}, windows={"features": ["windows"]})
    config.platforms = ["linux-64"]
    config.features["windows"].platforms = ["win-64"]
    installed: list[str] = []
    locked: list[dict] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        lambda ctx, resolved, **kwargs: installed.append(resolved.name),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda ctx, resolved_envs, **kwargs: locked.append(resolved_envs),
    )

    sync_environments(
        config,
        fake_ctx,
        [selected_name],
        no_install=no_install,
        console=captured_console,
    )

    assert installed == expected_installed
    assert set(locked[0]) == {"default", "windows"}
    assert locked[0]["windows"].platforms == ["win-64"]


def test_sync_validates_selected_platforms_before_installing(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(default={}, windows={"features": ["windows"]})
    config.platforms = ["linux-64"]
    config.features["windows"].platforms = ["win-64"]
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        lambda *args, **kwargs: pytest.fail("installed before platform validation"),
    )

    with pytest.raises(PlatformError):
        sync_environments(
            config,
            fake_ctx,
            ["default", "windows"],
            console=captured_console,
        )


def test_force_dry_run_reuses_preview_prefix_for_lock_solve(
    tmp_path: Path,
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_prefix = tmp_path / ".test.dry-run"
    install_calls: list[dict[str, object]] = []
    lock_calls: list[tuple[dict, dict[str, object]]] = []

    def fake_install(ctx, resolved, **kwargs):
        install_calls.append(kwargs)
        return preview_prefix

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        fake_install,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda ctx, resolved_envs, **kwargs: lock_calls.append((resolved_envs, kwargs)),
    )

    sync_environments(
        _config(default={}, test={"features": ["test"]}),
        fake_ctx,
        ["test"],
        force_reinstall=True,
        dry_run=True,
        console=captured_console,
    )

    assert install_calls == [{"force_reinstall": True, "dry_run": True}]
    resolved_envs, lock_kwargs = lock_calls[0]
    assert set(resolved_envs) == {"default", "test"}
    assert lock_kwargs["solve_prefixes"] == {"test": preview_prefix}


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
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda ctx, resolved_envs, **kwargs: None,
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
