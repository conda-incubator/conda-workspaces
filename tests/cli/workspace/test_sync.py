"""Tests for conda_workspaces.cli.workspace.sync."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from conda.base.context import context as conda_context
from rich.console import Console

import conda_workspaces.cli.workspace.sync as sync_module
from conda_workspaces.cli.workspace.sync import (
    affected_environments,
    sync_environments,
)
from conda_workspaces.exceptions import (
    CondaWorkspacesError,
    EnvironmentNotFoundError,
    PlatformError,
)
from conda_workspaces.models import Environment, Feature, WorkspaceConfig

_RENDERED_LOCK = """\
version: 1
environments: {}
packages: []
"""


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


@pytest.mark.parametrize(
    ("target", "expected"),
    [("dev", ["dev"]), ("default", ["default"]), ("missing", [])],
)
def test_affected_environment_target(target: str, expected: list[str]) -> None:
    config = _config(default={}, dev={"features": ["shared"]})
    assert (
        affected_environments(
            config,
            None,
            target_environment=target,
        )
        == expected
    )


@pytest.fixture
def captured_console() -> Console:
    """A Console that writes to StringIO so we can inspect output."""
    return Console(file=StringIO(), width=200)


@pytest.fixture
def fake_ctx(tmp_path: Path):
    """A minimal ``WorkspaceContext`` stand-in using *tmp_path* as the env prefix."""

    class FakeCtx:
        platform = "linux-64"
        root = tmp_path
        config = WorkspaceConfig(
            root=str(tmp_path),
            manifest_path=str(tmp_path / "pixi.toml"),
        )

        def env_prefix(self, name: str):
            return tmp_path

    return FakeCtx()


@pytest.fixture
def sync_calls(
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
) -> list[str]:
    """Stub resolve / install / lockfile helpers and record which ran."""
    calls: list[str] = []

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        lambda *a, **k: calls.append("install"),
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, *_args: calls.append(
            "preflight" if phase == "prepare" else "install"
        ),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda *a, **k: calls.append("render") or _RENDERED_LOCK,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.write_lockfile",
        lambda *a, **k: calls.append("write"),
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


def test_sync_progress_does_not_emit_terminal_controls(
    captured_console: Console,
    fake_ctx,
    sync_calls: list[str],
) -> None:
    payload = "spoof\x1b[2J\x1b]8;;https://example.invalid\x1b\\"

    sync_environments(
        _config(**{payload: {}}),
        fake_ctx,
        [payload],
        console=captured_console,
    )

    output = captured_console.file.getvalue()
    assert "\x1b[2J" not in output
    assert "\x1b]8;;https://example.invalid" not in output
    assert r"\x1b[2J" in output


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
        ({}, ["render", "preflight", "write", "install"]),
        ({"no_install": True}, ["render", "write"]),
        ({"dry_run": True}, ["render", "preflight"]),
        ({"no_install": True, "dry_run": True}, ["render"]),
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
    """Publish desired state before an enabled prefix installation."""
    sync_environments(
        _config(default={}),
        fake_ctx,
        ["default"],
        console=captured_console,
        **flags,
    )
    assert sync_calls == expected_calls


def test_sync_threads_required_absence_to_install_preflight(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
) -> None:
    prepare_calls: dict[str, dict[str, object]] = {}

    def validate_workspace() -> None:
        pass

    def record_prepare(
        phase: str,
        _ctx: object,
        name: str,
        kwargs: dict[str, object],
    ) -> None:
        if phase == "prepare":
            prepare_calls[name] = kwargs

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        record_prepare,
    )
    monkeypatch.setattr(
        sync_module,
        "render_lockfile",
        lambda *args, **kwargs: _RENDERED_LOCK,
    )

    sync_environments(
        _config(default={}, imported={}),
        fake_ctx,
        ["default", "imported"],
        dry_run=True,
        require_absent_prefixes=["imported"],
        validate_workspace=validate_workspace,
        console=captured_console,
    )

    assert prepare_calls["default"]["require_absent"] is False
    assert prepare_calls["imported"]["require_absent"] is True
    assert prepare_calls["default"]["validate_workspace"] is validate_workspace
    assert prepare_calls["imported"]["validate_workspace"] is validate_workspace


@pytest.mark.parametrize("prefix_exists", [False, True], ids=["absent", "present"])
def test_sync_requires_selected_prefixes_absent_without_install(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prefix_exists: bool,
) -> None:
    prefix = tmp_path / "envs" / "default"
    if prefix_exists:
        prefix.mkdir(parents=True)
    monkeypatch.setattr(fake_ctx, "env_prefix", lambda _name: prefix)
    monkeypatch.setattr(
        sync_module,
        "render_lockfile",
        lambda *args, **kwargs: _RENDERED_LOCK,
    )
    published: list[str] = []

    if prefix_exists:
        with pytest.raises(CondaWorkspacesError, match="prefix already exists"):
            sync_environments(
                _config(default={}),
                fake_ctx,
                ["default"],
                no_install=True,
                publish_lockfile=published.append,
                require_absent_prefixes=["default"],
                console=captured_console,
            )
    else:
        sync_environments(
            _config(default={}),
            fake_ctx,
            ["default"],
            no_install=True,
            publish_lockfile=published.append,
            require_absent_prefixes=["default"],
            console=captured_console,
        )

    assert published == ([] if prefix_exists else [_RENDERED_LOCK])


def test_sync_lock_failure_prevents_prefix_install(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_render(*args, **kwargs):
        calls.append("render")
        raise RuntimeError("lock solve failed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        fail_render,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        lambda *args, **kwargs: calls.append("install"),
    )

    with pytest.raises(RuntimeError, match="lock solve failed"):
        sync_environments(
            _config(default={}),
            fake_ctx,
            ["default"],
            console=captured_console,
        )

    assert calls == ["render"]


def test_sync_rejects_lockfile_changed_during_rendering(
    tmp_path: Path,
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(default={})
    config.root = str(tmp_path)
    config.manifest_path = str(tmp_path / "pixi.toml")
    fake_ctx.config = config
    output = tmp_path / "conda.lock"
    output.write_text("original", encoding="utf-8")
    concurrent = "concurrent"

    def replace_output(*args, **kwargs) -> str:
        output.write_text(concurrent, encoding="utf-8")
        return _RENDERED_LOCK

    monkeypatch.setattr(sync_module, "render_lockfile", replace_output)

    with pytest.raises(ValueError, match="changed before writing"):
        sync_environments(
            config,
            fake_ctx,
            ["default"],
            no_install=True,
            console=captured_console,
        )

    assert output.read_text(encoding="utf-8") == concurrent


def test_sync_dry_run_reuses_package_cache_across_pipeline(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    replace_lockfile_install_plan,
) -> None:
    configured_cache = tmp_path / "configured-pkgs"
    cache_paths: list[Path] = []

    def record_cache(*_args: object, **_kwargs: object) -> None:
        cache_paths.append(Path(conda_context.pkgs_dirs[0]))

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda *_args, **_kwargs: record_cache() or _RENDERED_LOCK,
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda *_args: record_cache(),
    )

    with conda_context._override("_pkgs_dirs", (str(configured_cache),)):
        sync_environments(
            _config(default={}),
            fake_ctx,
            ["default"],
            dry_run=True,
            console=captured_console,
        )
        assert conda_context.pkgs_dirs == (str(configured_cache),)

    assert len(cache_paths) == 2
    assert cache_paths[0] == cache_paths[1]
    assert cache_paths[0] != configured_cache
    assert not cache_paths[0].exists()


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
    replace_lockfile_install_plan,
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
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, _ctx, name, _kwargs: (
            installed.append(name) if phase == "execute" else None
        ),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: (
            locked.append(resolved_envs) or _RENDERED_LOCK
        ),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.write_lockfile",
        lambda *args, **kwargs: None,
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


def test_force_dry_run_validates_rendered_lock_without_removal(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
) -> None:
    install_calls: list[dict[str, object]] = []
    lock_calls: list[tuple[dict, dict[str, object]]] = []

    def fake_install(phase, ctx, name, kwargs):
        assert phase == "prepare"
        install_calls.append(kwargs)

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        fake_install,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: (
            lock_calls.append((resolved_envs, kwargs)),
            _RENDERED_LOCK,
        )[-1],
    )

    sync_environments(
        _config(default={}, test={"features": ["test"]}),
        fake_ctx,
        ["test"],
        force_reinstall=True,
        dry_run=True,
        console=captured_console,
    )

    assert install_calls == [
        {
            "lockfile_data": {
                "version": 1,
                "environments": {},
                "packages": [],
            },
            "update_names": None,
            "prune": False,
            "replace_existing": True,
            "require_absent": False,
            "validate_workspace": None,
        }
    ]
    resolved_envs, lock_kwargs = lock_calls[0]
    assert set(resolved_envs) == {"default", "test"}
    assert lock_kwargs["dry_run"] is True
    solve_prefixes = lock_kwargs["solve_prefixes"]
    assert set(solve_prefixes) == {"test"}
    assert solve_prefixes["test"].name == "test"
    assert not solve_prefixes["test"].exists()


@pytest.mark.parametrize(
    "prefix_identity",
    [(7, 11), None],
    ids=["existing-prefix", "absent-prefix"],
)
def test_force_reinstall_only_removes_preflight_prefix(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    prefix_identity: tuple[int, int] | None,
    replace_lockfile_install_plan,
) -> None:
    events: list[tuple[str, object]] = []

    def record_install(phase, ctx, name, kwargs) -> None:
        if phase == "prepare":
            events.append(("prepare", kwargs["replace_existing"]))
        elif phase == "remove":
            events.append(("remove", kwargs["expected_prefix_identity"]))
        else:
            events.append(("execute", None))

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        record_install,
        preflight_prefix_identity=prefix_identity,
    )
    monkeypatch.setattr(
        sync_module,
        "render_lockfile",
        lambda *args, **kwargs: _RENDERED_LOCK,
    )

    sync_environments(
        _config(default={}),
        fake_ctx,
        ["default"],
        force_reinstall=True,
        publish_lockfile=lambda content: events.append(("publish", content)),
        console=captured_console,
    )

    expected = [("prepare", True), ("publish", _RENDERED_LOCK)]
    if prefix_identity is not None:
        expected.append(("remove", prefix_identity))
    expected.append(("execute", None))
    assert events == expected


def test_sync_selective_update_threads_host_and_lock_targets(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
) -> None:
    config = _config(default={})
    config.platforms = ["linux-64", "win-64"]
    fake_ctx.config = config
    updates = {
        ("default", "linux-64"): {"python"},
        ("default", "win-64"): {"python"},
    }
    install_calls: list[dict[str, object]] = []
    exact_install_calls: list[dict[str, object]] = []
    render_calls: list[dict[str, object]] = []
    write_calls: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        lambda ctx, resolved, **kwargs: install_calls.append(kwargs),
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, ctx, name, kwargs: exact_install_calls.append(
            {"phase": phase, **kwargs}
        ),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved, **kwargs: render_calls.append(kwargs) or _RENDERED_LOCK,
    )
    sync_environments(
        config,
        fake_ctx,
        ["default"],
        baseline_lockfile={"version": 1},
        update_targets=updates,
        publish_lockfile=write_calls.append,
        console=captured_console,
    )

    assert install_calls == [{"dry_run": True, "update_names": {"python"}}]
    assert exact_install_calls == [
        {
            "phase": "prepare",
            "lockfile_data": {
                "version": 1,
                "environments": {},
                "packages": [],
            },
            "update_names": {"python"},
            "prune": False,
            "replace_existing": False,
            "require_absent": False,
            "validate_workspace": None,
        },
        {
            "phase": "execute",
            "lockfile_data": {
                "version": 1,
                "environments": {},
                "packages": [],
            },
            "update_names": {"python"},
            "prune": False,
            "replace_existing": False,
            "require_absent": False,
            "validate_workspace": None,
        },
    ]
    assert render_calls[0]["baseline_data"] == {"version": 1}
    assert render_calls[0]["update_targets"] == updates
    assert render_calls[0]["dry_run"] is True
    assert write_calls == [_RENDERED_LOCK]


def test_sync_selective_lock_only_needs_no_host_environment(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(windows={})
    config.platforms = ["win-64"]
    fake_ctx.config = config
    render_calls: list[dict[str, object]] = []
    write_calls: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        lambda *args, **kwargs: pytest.fail("unexpected prefix install"),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved, **kwargs: render_calls.append(kwargs) or _RENDERED_LOCK,
    )
    sync_environments(
        config,
        fake_ctx,
        [],
        no_install=True,
        baseline_lockfile={"version": 1},
        update_targets={("windows", "win-64"): {"python"}},
        publish_lockfile=write_calls.append,
        console=captured_console,
    )

    assert render_calls[0]["update_targets"] == {("windows", "win-64"): {"python"}}
    assert write_calls == [_RENDERED_LOCK]


def test_sync_selective_update_preflights_all_before_publication(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
) -> None:
    config = _config(default={}, dev={})
    config.platforms = ["linux-64"]
    fake_ctx.config = config
    updates = {
        ("default", "linux-64"): {"python"},
        ("dev", "linux-64"): {"python"},
    }
    calls: list[str] = []

    def fake_preview(ctx, resolved, **kwargs):
        calls.append(f"preview-{resolved.name}")

    def fake_exact_install(phase, ctx, name, kwargs):
        action = "preflight" if phase == "prepare" else "install"
        calls.append(f"{action}-{name}")
        if name == "dev" and phase == "prepare":
            raise RuntimeError("transaction failed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        fake_preview,
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        fake_exact_install,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda *args, **kwargs: calls.append("render") or _RENDERED_LOCK,
    )
    with pytest.raises(RuntimeError, match="transaction failed"):
        sync_environments(
            config,
            fake_ctx,
            ["default", "dev"],
            baseline_lockfile={"version": 1},
            update_targets=updates,
            publish_lockfile=lambda content: calls.append("write"),
            console=captured_console,
        )

    assert calls == [
        "preview-default",
        "preview-dev",
        "render",
        "preflight-default",
        "preflight-dev",
    ]


@pytest.mark.parametrize(
    ("failure_stage", "message", "expected_calls"),
    [
        ("render", "lock solve failed", ["preview", "render"]),
        (
            "publish",
            "publication failed",
            ["preview", "render", "preflight", "publish"],
        ),
    ],
    ids=["lock-solve", "publication"],
)
def test_sync_selective_update_failure_prevents_real_install(
    captured_console: Console,
    fake_ctx,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    message: str,
    expected_calls: list[str],
    replace_lockfile_install_plan,
) -> None:
    config = _config(default={})
    config.platforms = ["linux-64"]
    fake_ctx.config = config
    calls: list[str] = []

    def fake_install(ctx, resolved, **kwargs):
        calls.append("preview" if kwargs["dry_run"] else "install")

    def render(*args, **kwargs) -> str:
        calls.append("render")
        if failure_stage == "render":
            raise RuntimeError(message)
        return _RENDERED_LOCK

    def publish(content: str) -> None:
        calls.append("publish")
        if failure_stage == "publish":
            raise RuntimeError(message)

    def fake_exact_install(phase, ctx, name, kwargs):  # type: ignore[no-untyped-def]
        calls.append("preflight" if phase == "prepare" else "install")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        fake_install,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        render,
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        fake_exact_install,
    )

    with pytest.raises(RuntimeError, match=message):
        sync_environments(
            config,
            fake_ctx,
            ["default"],
            baseline_lockfile={"version": 1},
            update_targets={("default", "linux-64"): {"python"}},
            publish_lockfile=publish,
            console=captured_console,
        )

    assert calls == expected_calls


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
    replace_lockfile_install_plan,
) -> None:
    """A new activate.d script only prints the re-spawn hint inside a spawned shell."""
    activate_d = tmp_path / "etc" / "conda" / "activate.d"

    def fake_install(
        ctx,
        resolved,
        *,
        force_reinstall=False,
        dry_run=False,
        prune=False,
        update_names=None,
    ):
        if dry_run:
            return
        activate_d.mkdir(parents=True, exist_ok=True)
        (activate_d / "pkg-activate.sh").write_text("# hook")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: _RENDERED_LOCK,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.write_lockfile",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment", fake_install
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, *_args: fake_install(None, None, dry_run=phase == "prepare"),
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
