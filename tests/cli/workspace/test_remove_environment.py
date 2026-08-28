"""Tests for removing complete workspace environments."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import tomlkit
from conda.base.context import context as conda_context
from conda.exceptions import CondaSystemExit

from conda_workspaces.cli.workspace.remove import execute_remove
from conda_workspaces.envs import remove_environment
from conda_workspaces.exceptions import CondaWorkspacesError
from conda_workspaces.manifests import detect_task_file
from conda_workspaces.publication import WorkspacePublication

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from tests.conftest import CreateWorkspaceEnv, SnapshotTree


_DEFAULTS = {
    "manifest_file": None,
    "specs": [],
    "pypi": False,
    "feature": None,
    "environment": None,
    "platform": None,
    "all": False,
    "no_install": False,
    "no_lockfile_update": False,
    "force_reinstall": False,
    "dry_run": False,
}


@pytest.fixture
def removal_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a workspace with a removable environment and a shared feature."""
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "remove-environment-test"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"

[feature.test.dependencies]
pytest = ">=8"

[environments]
default = []
test = {features = ["test"]}
other = {features = ["test"]}
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return path


@pytest.fixture
def stub_environment_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, set[str], bool | None]]:
    """Record the prospective environment set used for lock generation."""
    calls: list[tuple[str, set[str], bool | None]] = []

    def resolve(config):
        calls.append(("resolve", set(config.environments), None))
        return {}

    def render(ctx, resolved, *, config, dry_run=False, **kwargs):
        calls.append(("render", set(config.environments), dry_run))
        return "version: 1\nenvironments: {}\npackages: []\n"

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.resolve_all_environments",
        resolve,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.render_lockfile",
        render,
    )
    return calls


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"all": False}, "Package names are required"),
        ({"environment": None}, "--all requires -e/--environment"),
        ({"specs": ["numpy"]}, "package specs"),
        ({"feature": "test"}, "--feature"),
        ({"platform": "linux-64"}, "--platform"),
        ({"pypi": True}, "--pypi"),
        ({"no_install": True}, "--no-install"),
        ({"no_lockfile_update": True}, "--no-lockfile-update"),
        ({"force_reinstall": True}, "--force-reinstall"),
    ],
    ids=[
        "missing-all",
        "missing-environment",
        "specs",
        "feature",
        "platform",
        "pypi",
        "no-install",
        "no-lockfile-update",
        "force-reinstall",
    ],
)
def test_remove_all_validates_operation_selection(
    removal_workspace: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    before = removal_workspace.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=removal_workspace,
        **{"environment": "test", "all": True, **overrides},
    )

    with pytest.raises(CondaWorkspacesError, match=message):
        execute_remove(args)

    assert removal_workspace.read_bytes() == before
    assert not removal_workspace.with_name("conda.lock").exists()


def test_yes_does_not_select_complete_environment_removal(
    removal_workspace: Path,
) -> None:
    before = removal_workspace.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=removal_workspace,
        environment="test",
    )

    with conda_context._override("always_yes", True):
        with pytest.raises(CondaWorkspacesError, match="Package names are required"):
            execute_remove(args)

    assert removal_workspace.read_bytes() == before


@pytest.mark.parametrize(
    ("filename", "namespace"),
    [
        ("conda.toml", None),
        ("pixi.toml", None),
        ("pyproject.toml", "conda"),
        ("pyproject.toml", "pixi"),
    ],
    ids=["conda-toml", "pixi-toml", "pyproject-conda", "pyproject-pixi"],
)
def test_remove_all_updates_each_supported_manifest_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_environment_lock: list[tuple[str, set[str], bool | None]],
    filename: str,
    namespace: str | None,
) -> None:
    owner = f"tool.{namespace}." if namespace is not None else ""
    path = tmp_path / filename
    path.write_text(
        f"""\
[{owner}workspace]
name = "formats"
channels = ["conda-forge"]
platforms = ["linux-64"]

[{owner}feature.shared.dependencies]
pytest = "*"

[{owner}environments]
default = []
test = {{features = ["shared"]}}
other = {{features = ["shared"]}}
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert (
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=path,
                environment="test",
                **{"all": True},
            )
        )
        == 0
    )

    document = tomlkit.loads(path.read_text(encoding="utf-8"))
    source = document if namespace is None else document["tool"][namespace]
    assert set(source["environments"]) == {"default", "other"}
    assert source["feature"]["shared"]["dependencies"]["pytest"] == "*"
    assert stub_environment_lock == [
        ("resolve", {"default", "other"}, None),
        ("render", {"default", "other"}, False),
    ]
    assert (
        path.with_name("conda.lock")
        .read_text(encoding="utf-8")
        .startswith("version: 1")
    )


def test_remove_all_rejects_implicit_default_environment(
    removal_workspace: Path,
) -> None:
    before = removal_workspace.read_bytes()

    with pytest.raises(CondaWorkspacesError, match="implicit 'default'"):
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="default",
                **{"all": True},
            )
        )

    assert removal_workspace.read_bytes() == before


def test_remove_all_reports_every_task_environment_reference(
    removal_workspace: Path,
) -> None:
    removal_workspace.write_text(
        removal_workspace.read_text(encoding="utf-8")
        + """\

[tasks.prepare]
cmd = "python -V"

[tasks.check]
cmd = "pytest"
default-environment = "test"
depends-on = [{task = "prepare", environment = "test"}]

[target.linux-64.tasks.check]
depends-on = [{task = "prepare", environment = "test"}]
""",
        encoding="utf-8",
    )
    before = removal_workspace.read_bytes()

    with pytest.raises(
        CondaWorkspacesError,
        match="still referenced by workspace tasks",
    ) as caught:
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )

    assert len(caught.value.hints) == 3
    assert any("default-environment" in hint for hint in caught.value.hints)
    assert any("depends-on entry 1" in hint for hint in caught.value.hints)
    assert any("target 'linux-64'" in hint for hint in caught.value.hints)
    assert removal_workspace.read_bytes() == before
    assert not removal_workspace.with_name("conda.lock").exists()


def test_remove_all_rejects_reference_from_separate_project_task_manifest(
    removal_workspace: Path,
) -> None:
    task_manifest = removal_workspace.with_name("conda.toml")
    task_manifest.write_text(
        """\
[tasks.check]
cmd = "pytest"
default-environment = "test"
""",
        encoding="utf-8",
    )
    workspace_before = removal_workspace.read_bytes()
    task_before = task_manifest.read_bytes()

    with pytest.raises(
        CondaWorkspacesError,
        match="still referenced by workspace tasks",
    ) as caught:
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )

    assert caught.value.hints == ["Task 'check' sets default-environment to 'test'."]
    assert removal_workspace.read_bytes() == workspace_before
    assert task_manifest.read_bytes() == task_before
    assert not removal_workspace.with_name("conda.lock").exists()


def test_remove_all_rejects_feature_only_task_environment_reference(
    removal_workspace: Path,
) -> None:
    removal_workspace.write_text(
        removal_workspace.read_text(encoding="utf-8")
        + """\

[feature.test.tasks.check]
cmd = "pytest"
default-environment = "test"
""",
        encoding="utf-8",
    )
    before = removal_workspace.read_bytes()
    assert detect_task_file(removal_workspace.parent) is None

    with pytest.raises(
        CondaWorkspacesError,
        match="still referenced by workspace tasks",
    ) as caught:
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )

    assert caught.value.hints == ["Task 'check' sets default-environment to 'test'."]
    assert removal_workspace.read_bytes() == before
    assert not removal_workspace.with_name("conda.lock").exists()


def test_remove_all_rejects_target_only_task_environment_reference(
    removal_workspace: Path,
) -> None:
    removal_workspace.write_text(
        removal_workspace.read_text(encoding="utf-8")
        + """\

[target.linux-64.tasks.prepare]
cmd = "python -V"

[target.linux-64.tasks.check]
cmd = "pytest"
depends-on = [{task = "prepare", environment = "test"}]
""",
        encoding="utf-8",
    )
    before = removal_workspace.read_bytes()
    assert detect_task_file(removal_workspace.parent) is None

    with pytest.raises(
        CondaWorkspacesError,
        match="still referenced by workspace tasks",
    ) as caught:
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )

    assert any("Task 'check' depends-on entry 1" in hint for hint in caught.value.hints)
    assert any("target 'linux-64'" in hint for hint in caught.value.hints)
    assert removal_workspace.read_bytes() == before
    assert not removal_workspace.with_name("conda.lock").exists()


def test_remove_all_ignores_user_task_environment_references(
    removal_workspace: Path,
    stub_environment_lock: list[tuple[str, set[str], bool | None]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user_task_manifest = tmp_path / "user-config" / "conda" / "tasks.toml"
    user_task_manifest.parent.mkdir(parents=True)
    user_task_manifest.write_text(
        """\
[tasks.check]
cmd = "pytest"
default-environment = "test"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_task_manifest.parents[1]))

    assert (
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )
        == 0
    )

    document = tomlkit.loads(removal_workspace.read_text(encoding="utf-8"))
    assert "test" not in document["environments"]
    assert user_task_manifest.is_file()
    assert stub_environment_lock[-1][0] == "render"


def test_remove_all_rejects_active_environment_before_lock_generation(
    removal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = removal_workspace.read_bytes()
    inspected: list[str] = []

    def active(prefix: str) -> bool:
        inspected.append(prefix)
        return True

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.is_active_prefix",
        active,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.render_lockfile",
        lambda *args, **kwargs: pytest.fail(
            "active environment reached lock generation"
        ),
    )

    with pytest.raises(CondaWorkspacesError, match="active and cannot be removed"):
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )

    assert inspected == [str(removal_workspace.parent / ".conda" / "envs" / "test")]
    assert removal_workspace.read_bytes() == before


def test_remove_all_dry_run_describes_every_state_without_writing(
    removal_workspace: Path,
    stub_environment_lock: list[tuple[str, set[str], bool | None]],
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    tmp_workspace_env: CreateWorkspaceEnv,
    snapshot_tree: SnapshotTree,
) -> None:
    prefix = tmp_workspace_env(removal_workspace.parent, "test")
    before = snapshot_tree(removal_workspace.parent)
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.confirm_yn",
        lambda *args, **kwargs: pytest.fail("dry-run requested confirmation"),
    )

    assert (
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                dry_run=True,
                **{"all": True},
            ),
            console=rich_console,
        )
        == 0
    )

    assert snapshot_tree(removal_workspace.parent) == before
    assert stub_environment_lock == [
        ("resolve", {"default", "other"}, None),
        ("render", {"default", "other"}, True),
    ]
    output = rich_console.file.getvalue()
    assert "Would remove" in output
    assert "environments.test" in output
    assert "all platforms" in output
    assert "conda.lock" in output
    assert str(prefix) in output
    assert "installed" in output


def test_remove_all_deletes_prefix_before_publishing_manifest_and_lock(
    removal_workspace: Path,
    stub_environment_lock: list[tuple[str, set[str], bool | None]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    prefix = tmp_workspace_env(removal_workspace.parent, "test")
    events: list[str] = []
    original_publish_lockfile = WorkspacePublication.publish_lockfile

    def confirm(*args, **kwargs) -> None:
        events.append("confirm")

    def remove(*args, **kwargs) -> None:
        remove_environment(*args, **kwargs)
        events.append("remove")

    def publish(self, content: str) -> None:
        assert not prefix.exists()
        events.append("publish")
        original_publish_lockfile(self, content)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.confirm_yn",
        confirm,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.remove_environment",
        remove,
    )
    monkeypatch.setattr(WorkspacePublication, "publish_lockfile", publish)
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)

    assert (
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )
        == 0
    )

    assert events == ["confirm", "remove", "publish"]
    assert not prefix.exists()
    document = tomlkit.loads(removal_workspace.read_text(encoding="utf-8"))
    assert "test" not in document["environments"]
    assert "test" in document["feature"]
    assert (
        removal_workspace.with_name("conda.lock")
        .read_text(encoding="utf-8")
        .startswith("version: 1")
    )
    assert stub_environment_lock == [
        ("resolve", {"default", "other"}, None),
        ("render", {"default", "other"}, False),
    ]


def test_remove_all_yes_skips_interactive_confirmation(
    removal_workspace: Path,
    stub_environment_lock: list[tuple[str, set[str], bool | None]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    prefix = tmp_workspace_env(removal_workspace.parent, "test")
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)

    with conda_context._override("always_yes", True):
        assert (
            execute_remove(
                make_args(
                    _DEFAULTS,
                    manifest_file=removal_workspace,
                    environment="test",
                    **{"all": True},
                )
            )
            == 0
        )

    assert not prefix.exists()
    assert stub_environment_lock[-1] == (
        "render",
        {"default", "other"},
        False,
    )


def test_remove_all_lock_generation_failure_preserves_workspace_state(
    removal_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    prefix = tmp_workspace_env(removal_workspace.parent, "test")
    lock_path = removal_workspace.with_name("conda.lock")
    lock_path.write_text("old lock\n", encoding="utf-8")
    manifest_before = removal_workspace.read_bytes()
    lock_before = lock_path.read_bytes()
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.resolve_all_environments",
        lambda config: {},
    )

    def fail_render(*args, **kwargs):
        raise RuntimeError("lock generation failed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.render_lockfile",
        fail_render,
    )

    with pytest.raises(RuntimeError, match="lock generation failed"):
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )

    assert prefix.is_dir()
    assert removal_workspace.read_bytes() == manifest_before
    assert lock_path.read_bytes() == lock_before


def test_remove_all_confirmation_failure_preserves_workspace_state(
    removal_workspace: Path,
    stub_environment_lock: list[tuple[str, set[str], bool | None]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    prefix = tmp_workspace_env(removal_workspace.parent, "test")
    lock_path = removal_workspace.with_name("conda.lock")
    lock_path.write_text("old lock\n", encoding="utf-8")
    manifest_before = removal_workspace.read_bytes()
    lock_before = lock_path.read_bytes()

    def abort(*args, **kwargs):
        raise CondaSystemExit()

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.confirm_yn",
        abort,
    )

    with pytest.raises(CondaSystemExit):
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )

    assert prefix.is_dir()
    assert removal_workspace.read_bytes() == manifest_before
    assert lock_path.read_bytes() == lock_before
    assert stub_environment_lock[-1][0] == "render"


def test_remove_all_prefix_failure_preserves_manifest_and_lockfile(
    removal_workspace: Path,
    stub_environment_lock: list[tuple[str, set[str], bool | None]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    prefix = tmp_workspace_env(removal_workspace.parent, "test")
    lock_path = removal_workspace.with_name("conda.lock")
    lock_path.write_text("old lock\n", encoding="utf-8")
    manifest_before = removal_workspace.read_bytes()
    lock_before = lock_path.read_bytes()
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.confirm_yn",
        lambda *args, **kwargs: None,
    )

    def fail_removal(*args, **kwargs):
        raise RuntimeError("prefix removal failed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.remove_environment",
        fail_removal,
    )

    with pytest.raises(RuntimeError, match="prefix removal failed"):
        execute_remove(
            make_args(
                _DEFAULTS,
                manifest_file=removal_workspace,
                environment="test",
                **{"all": True},
            )
        )

    assert prefix.is_dir()
    assert removal_workspace.read_bytes() == manifest_before
    assert lock_path.read_bytes() == lock_before
    assert stub_environment_lock[-1][0] == "render"
