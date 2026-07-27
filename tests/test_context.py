"""Tests for conda_workspaces.context."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING

import pytest
from conda import __version__ as conda_version
from conda.base.context import Context, context

from conda_workspaces.context import (
    CondaContext,
    WorkspaceContext,
    build_template_context,
    isolated_package_cache,
)
from conda_workspaces.exceptions import (
    CondaWorkspacesError,
    EnvironmentNameInvalidError,
)
from conda_workspaces.models import (
    Channel,
    Environment,
    Feature,
    WorkspaceConfig,
)

if TYPE_CHECKING:
    from tests.conftest import CreateWorkspaceEnv


@pytest.fixture
def config(tmp_path: Path) -> WorkspaceConfig:
    """Minimal workspace config rooted in tmp_path."""
    return WorkspaceConfig(
        name="ctx-test",
        root=str(tmp_path),
        channels=[Channel("conda-forge")],
        platforms=["linux-64", "osx-arm64", "win-64"],
        features={"default": Feature(name="default")},
        environments={"default": Environment(name="default")},
    )


def test_config_passthrough(config: WorkspaceConfig) -> None:
    ctx = WorkspaceContext(config)
    assert ctx.config is config


def test_isolated_package_cache_reuses_nested_scratch(tmp_path: Path) -> None:
    configured_cache = tmp_path / "configured-pkgs"
    repodata_cache = configured_cache / "cache"
    repodata_cache.mkdir(parents=True)
    (repodata_cache / "cached.json").write_bytes(b"cached")

    with context._override("_pkgs_dirs", (str(configured_cache),)):
        with isolated_package_cache(True):
            scratch_cache = Path(context.pkgs_dirs[0])
            assert scratch_cache != configured_cache
            assert (scratch_cache / "cache" / "cached.json").read_bytes() == b"cached"

            with isolated_package_cache(True):
                assert Path(context.pkgs_dirs[0]) == scratch_cache

        assert context.pkgs_dirs == (str(configured_cache),)

    assert not scratch_cache.exists()


def test_isolated_package_cache_serializes_threads(tmp_path: Path) -> None:
    configured_cache = tmp_path / "configured-pkgs"
    second_attempting = Event()
    second_entered = Event()
    release_second = Event()
    second_scratch: list[Path] = []

    def isolate() -> None:
        second_attempting.set()
        with isolated_package_cache(True):
            second_scratch.append(Path(context.pkgs_dirs[0]))
            second_entered.set()
            release_second.wait(timeout=5)

    thread = Thread(target=isolate)
    with context._override("_pkgs_dirs", (str(configured_cache),)):
        with isolated_package_cache(True):
            first_scratch = Path(context.pkgs_dirs[0])
            thread.start()
            assert second_attempting.wait(timeout=5)
            blocked = not second_entered.wait(timeout=0.1)
            first_preserved = Path(context.pkgs_dirs[0]) == first_scratch

        entered_after_release = second_entered.wait(timeout=5)
        second_active = (
            bool(second_scratch) and Path(context.pkgs_dirs[0]) == second_scratch[0]
        )
        release_second.set()
        thread.join(timeout=5)
        restored = context.pkgs_dirs == (str(configured_cache),)

    assert blocked
    assert first_preserved
    assert entered_after_release
    assert second_active
    assert not thread.is_alive()
    assert first_scratch != second_scratch[0]
    assert not first_scratch.exists()
    assert not second_scratch[0].exists()
    assert restored


def test_root(config: WorkspaceConfig) -> None:
    ctx = WorkspaceContext(config)
    assert ctx.root == Path(config.root)


def test_envs_dir(config: WorkspaceConfig) -> None:
    ctx = WorkspaceContext(config)
    assert ctx.envs_dir == Path(config.root) / ".conda" / "envs"


def test_envs_dir_rejects_workspace_escape(config: WorkspaceConfig) -> None:
    config.envs_dir = "../outside"

    with pytest.raises(CondaWorkspacesError, match="escapes the workspace"):
        WorkspaceContext(config).envs_dir


@pytest.mark.parametrize(
    "precomputed",
    [False, True],
    ids=["initial", "after-first-access"],
)
def test_envs_dir_rejects_symlinked_state_directory(
    config: WorkspaceConfig,
    tmp_path: Path,
    precomputed: bool,
) -> None:
    ctx = WorkspaceContext(config)
    if precomputed:
        assert ctx.envs_dir == Path(config.root) / ".conda" / "envs"
    state = Path(config.root) / ".conda"
    target = tmp_path / "state"
    target.mkdir()
    state.symlink_to(target, target_is_directory=True)

    with pytest.raises(CondaWorkspacesError, match="contains a symlink"):
        ctx.envs_dir


def test_env_prefix_rejects_symlink_inside_envs_dir(
    config: WorkspaceConfig,
) -> None:
    ctx = WorkspaceContext(config)
    ctx.envs_dir.mkdir(parents=True)
    target = ctx.envs_dir / "target"
    target.mkdir()
    try:
        (ctx.envs_dir / "linked").symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(CondaWorkspacesError, match="prefix cannot be a symlink"):
        ctx.env_prefix("linked")


@pytest.mark.parametrize(
    "cache_key, value_a, value_b",
    [
        ("platform", "linux-64", "osx-arm64"),
        ("root_prefix", Path("/fake/root"), Path("/other/root")),
    ],
    ids=["platform", "root-prefix"],
)
def test_cached_property(
    config: WorkspaceConfig, cache_key: str, value_a, value_b
) -> None:
    """Cached properties return values from the internal cache."""
    ctx = WorkspaceContext(config)
    ctx._cache[cache_key] = value_a
    assert getattr(ctx, cache_key) == value_a
    ctx._cache[cache_key] = value_b
    assert getattr(ctx, cache_key) == value_b


def test_config_lazy_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """config property calls detect_and_parse when no config is given."""
    expected_config = WorkspaceConfig(
        name="lazy-test",
        root=str(tmp_path),
        channels=[Channel("conda-forge")],
        platforms=["linux-64"],
        features={"default": Feature(name="default")},
        environments={"default": Environment(name="default")},
    )

    def fake_detect_and_parse(path=None):
        return (tmp_path / "pixi.toml", expected_config)

    monkeypatch.setattr(
        "conda_workspaces.manifests.detect_and_parse", fake_detect_and_parse
    )

    ctx = WorkspaceContext()  # no config passed
    assert ctx.config.name == "lazy-test"


@pytest.mark.parametrize(
    "env_name",
    ["default", "test", "docs"],
    ids=["default", "named-test", "named-docs"],
)
def test_env_prefix(config: WorkspaceConfig, env_name: str) -> None:
    ctx = WorkspaceContext(config)
    prefix = ctx.env_prefix(env_name)
    assert prefix == ctx.envs_dir / env_name


@pytest.mark.parametrize(
    "env_name",
    [
        "",
        ".",
        "..",
        "../outside-prefix",
        "../../outside-prefix",
        "/tmp/outside-prefix",
        "nested/env",
        r"..\outside-prefix",
        r"nested\env",
        r"C:\outside-prefix",
        "C:outside-prefix",
        "NUL",
        "com1.txt",
        "default.",
        "default ",
        "default:stream",
    ],
    ids=[
        "empty",
        "dot",
        "dot-dot",
        "parent",
        "parents",
        "absolute",
        "nested-posix",
        "windows-parent",
        "nested-windows",
        "windows-absolute",
        "windows-drive-relative",
        "windows-device",
        "windows-device-extension",
        "windows-trailing-dot",
        "windows-trailing-space",
        "windows-alternate-data-stream",
    ],
)
def test_env_prefix_rejects_path_like_names(
    config: WorkspaceConfig,
    env_name: str,
) -> None:
    ctx = WorkspaceContext(config)
    with pytest.raises(EnvironmentNameInvalidError, match="not valid"):
        ctx.env_prefix(env_name)


@pytest.mark.parametrize(
    "has_conda_meta, expected",
    [
        (True, True),
        (False, False),
    ],
    ids=["exists", "missing"],
)
def test_env_exists(
    config: WorkspaceConfig,
    has_conda_meta: bool,
    expected: bool,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    ctx = WorkspaceContext(config)
    if has_conda_meta:
        tmp_workspace_env(ctx.root, "default")
    assert ctx.env_exists("default") is expected


def test_conda_context_platform():
    assert CondaContext().platform == context.subdir


@pytest.mark.parametrize(
    ("on_win_val", "expected_win", "expected_unix"),
    [
        (False, False, True),
        (True, True, False),
    ],
)
def test_conda_context_is_win(monkeypatch, on_win_val, expected_win, expected_unix):
    monkeypatch.setattr("conda.base.constants.on_win", on_win_val)
    ctx = CondaContext()
    assert ctx.is_win is expected_win
    assert ctx.is_unix is expected_unix


def test_conda_context_is_osx():
    assert CondaContext().is_osx == (context.platform == "osx")


def test_conda_context_is_linux():
    assert CondaContext().is_linux == (context.platform == "linux")


@pytest.mark.parametrize(
    ("manifest_path", "expected"),
    [
        (
            Path("/some/project/conda.toml"),
            str(Path("/some/project/conda.toml")),
        ),
        (None, ""),
    ],
    ids=["with-path", "none"],
)
def test_conda_context_manifest_path(manifest_path, expected):
    ctx = CondaContext(manifest_path=manifest_path)
    assert ctx.manifest_path == expected


def test_conda_context_environment_name():
    ctx = CondaContext()
    if context.active_prefix:
        expected = Path(context.active_prefix).name
    else:
        expected = "base"
    assert ctx.environment_name == expected


def test_conda_context_environment_name_fallback(monkeypatch):
    monkeypatch.setattr(Context, "active_prefix", property(lambda self: ""))
    assert CondaContext().environment_name == "base"


def test_conda_context_environment_proxy():
    ctx = CondaContext()
    assert ctx.environment.name == ctx.environment_name


def test_conda_context_prefix():
    assert CondaContext().prefix == context.target_prefix


def test_conda_context_selected_prefix():
    selected_prefix = Path("/workspace/.conda/envs/test")
    ctx = CondaContext(target_prefix=selected_prefix)

    assert ctx.prefix == str(selected_prefix)
    assert ctx.environment_name == "test"
    assert ctx.environment.name == "test"


def test_conda_context_version():
    assert CondaContext().version == conda_version


def test_conda_context_init_cwd():
    assert CondaContext().init_cwd == os.getcwd()


def test_build_context_has_conda_and_pixi():
    ctx = build_template_context()
    assert "conda" in ctx
    assert "pixi" in ctx
    assert ctx["conda"] is ctx["pixi"]


def test_build_context_task_args():
    ctx = build_template_context(task_args={"path": "tests/"})
    assert ctx["path"] == "tests/"
