"""Tests for conda_workspaces.cli.workspace.clean."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conda.base.context import context as conda_context
from conda.exceptions import CondaSystemExit

from conda_workspaces.cli.workspace.clean import execute_clean
from conda_workspaces.exceptions import CondaWorkspacesError, EnvironmentNotFoundError

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from tests.conftest import CreateWorkspaceEnv, SnapshotTree

_DEFAULTS = {"manifest_file": None, "environment": None, "dry_run": False}


def _stub_confirm_and_unregister(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub confirm_yn (auto-yes) and unregister_env (no-op)."""
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.clean.confirm_yn", lambda *a, **kw: None
    )
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)


def test_clean_single_environment(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_confirm_and_unregister(monkeypatch)
    prefix = tmp_workspace_env(pixi_workspace, "default")
    assert prefix.is_dir()

    args = make_args(_DEFAULTS, environment="default")
    result = execute_clean(args)
    assert result == 0
    assert not prefix.is_dir()
    assert "Removed" in capsys.readouterr().out


def test_clean_single_orphaned_environment(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_confirm_and_unregister(monkeypatch)
    prefix = tmp_workspace_env(pixi_workspace, "orphan")

    result = execute_clean(make_args(_DEFAULTS, environment="orphan"))

    assert result == 0
    assert not prefix.exists()
    assert "Removed" in capsys.readouterr().out


def test_clean_all_environments(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    _stub_confirm_and_unregister(monkeypatch)
    tmp_workspace_env(pixi_workspace, "default")
    tmp_workspace_env(pixi_workspace, "test")
    unrelated = pixi_workspace / ".conda" / "envs" / "not-an-environment"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_bytes(b"keep")

    args = make_args(_DEFAULTS)
    result = execute_clean(args)
    assert result == 0
    assert (unrelated / "keep.txt").read_bytes() == b"keep"
    assert "Removed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "environment",
    ["default", None],
    ids=["single", "all-with-yes"],
)
def test_clean_dry_run_preserves_prefixes(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_workspace_env: CreateWorkspaceEnv,
    snapshot_tree: SnapshotTree,
    environment: str | None,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")
    tmp_workspace_env(pixi_workspace, "test")
    unrelated = pixi_workspace / ".conda" / "envs" / "not-an-environment"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_bytes(b"keep")
    before = snapshot_tree(pixi_workspace)

    with conda_context._override("always_yes", True):
        result = execute_clean(
            make_args(
                _DEFAULTS,
                environment=environment,
                dry_run=True,
            )
        )

    assert result == 0
    assert snapshot_tree(pixi_workspace) == before
    assert "Would remove" in capsys.readouterr().out


@pytest.mark.parametrize(
    "env_arg, expected_msg",
    [
        ("default", "not installed"),
        (None, "No environments"),
    ],
    ids=["single-not-installed", "none-installed"],
)
def test_clean_nothing_to_remove(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env_arg: str | None,
    expected_msg: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, environment=env_arg)
    result = execute_clean(args)
    assert result == 0
    assert expected_msg in capsys.readouterr().out


def test_clean_escapes_environment_markup(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    manifest = pixi_workspace / "pixi.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "default = []",
            '"[red]unsafe" = []',
        ),
        encoding="utf-8",
    )

    result = execute_clean(
        make_args(_DEFAULTS, environment="[red]unsafe"),
        console=rich_console,
    )

    assert result == 0
    assert "[red]unsafe" in rich_console.file.getvalue()


def test_clean_prompt_encodes_installed_environment_controls(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "unsafe\x85name")
    prompts: list[str] = []

    def record_and_abort(prompt: str) -> None:
        prompts.append(prompt)
        raise CondaSystemExit()

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.clean.confirm_yn",
        record_and_abort,
    )

    with conda_context._override("always_yes", False):
        result = execute_clean(make_args(_DEFAULTS), console=rich_console)

    assert result == 0
    assert prompts == [r"Remove unsafe\x85name environments?"]


@pytest.mark.parametrize(
    "env_arg",
    ["default", None],
    ids=["single-env", "all-envs"],
)
def test_clean_prompt_abort(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    env_arg: str | None,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    def raise_abort(*args, **kwargs):
        raise CondaSystemExit()

    monkeypatch.setattr("conda_workspaces.cli.workspace.clean.confirm_yn", raise_abort)
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)

    args = make_args(_DEFAULTS, environment=env_arg)
    with conda_context._override("always_yes", False):
        result = execute_clean(args)
    assert result == 0
    assert (pixi_workspace / ".conda" / "envs" / "default" / "conda-meta").is_dir()


@pytest.mark.parametrize(
    "env_arg",
    ["default", None],
    ids=["single-env", "all-envs"],
)
@pytest.mark.parametrize("mutation", ["rewrite", "replace"])
def test_clean_rejects_prefix_changed_during_confirmation(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    env_arg: str | None,
    mutation: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    prefix = tmp_workspace_env(pixi_workspace, "default", pkg_count=1)
    displaced = prefix.with_name("original-default")
    replacement = prefix.with_name("replacement-default")
    replacement.mkdir()
    (replacement / "post-confirmation.txt").write_bytes(b"preserve replacement")

    def change_prefix(_prompt: str) -> None:
        prefix.rename(displaced)
        if mutation == "rewrite":
            prefix.mkdir()
            (prefix / "post-confirmation.txt").write_bytes(b"preserve rewrite")
        else:
            replacement.rename(prefix)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.clean.confirm_yn",
        change_prefix,
    )

    with (
        conda_context._override("always_yes", False),
        pytest.raises(CondaWorkspacesError, match="changed before removal"),
    ):
        execute_clean(make_args(_DEFAULTS, environment=env_arg))

    assert (displaced / "conda-meta" / "pkg-0.json").read_bytes() == b"{}"
    assert (prefix / "post-confirmation.txt").read_bytes().startswith(b"preserve")


@pytest.mark.parametrize(
    "env_arg",
    ["default", None],
    ids=["single-env", "all-envs"],
)
def test_clean_rejects_envs_directory_changed_during_confirmation(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    env_arg: str | None,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    prefix = tmp_workspace_env(pixi_workspace, "default", pkg_count=1)
    envs_dir = prefix.parent
    displaced = envs_dir.with_name("original-envs")
    replacement = envs_dir.with_name("replacement-envs")
    replacement.mkdir()
    (replacement / "post-confirmation.txt").write_bytes(b"preserve replacement")

    def change_envs_directory(_prompt: str) -> None:
        envs_dir.rename(displaced)
        replacement.rename(envs_dir)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.clean.confirm_yn",
        change_envs_directory,
    )

    with (
        conda_context._override("always_yes", False),
        pytest.raises(CondaWorkspacesError, match="directory changed"),
    ):
        execute_clean(make_args(_DEFAULTS, environment=env_arg))

    assert (displaced / "default" / "conda-meta" / "pkg-0.json").read_bytes() == b"{}"
    assert (envs_dir / "post-confirmation.txt").read_bytes() == b"preserve replacement"


@pytest.mark.parametrize(
    "prefix_kind",
    ["missing", "not-an-environment"],
    ids=["missing", "invalid-prefix"],
)
def test_clean_rejects_undefined_non_environment(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    prefix_kind: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    if prefix_kind == "not-an-environment":
        prefix = pixi_workspace / ".conda" / "envs" / "nonexistent"
        prefix.mkdir(parents=True)
        (prefix / "keep.txt").write_bytes(b"keep")

    args = make_args(_DEFAULTS, environment="nonexistent")
    with pytest.raises(EnvironmentNotFoundError, match="not defined"):
        execute_clean(args)

    if prefix_kind == "not-an-environment":
        assert (prefix / "keep.txt").read_bytes() == b"keep"
