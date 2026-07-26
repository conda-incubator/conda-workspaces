"""Tests for conda_workspaces.envs."""

from __future__ import annotations

import builtins
import importlib.util
import logging
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.conftest import CreateWorkspaceEnv, SnapshotTree

from conda.base.constants import ChannelPriority, UpdateModifier
from conda.base.context import context as conda_context
from conda.core.envs_manager import PrefixData
from conda.exceptions import PackageNotInstalledError, UnsatisfiableError
from conda.models.records import PrefixRecord

import conda_workspaces.envs as envs_mod
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.envs import (
    _apply_activation_env,
    _apply_activation_scripts,
    _apply_system_requirements,
    _build_pypi_specs,
    _channel_priority_override,
    _install_path_deps,
    clean_all,
    get_environment_info,
    install_environment,
    list_installed_environments,
    remove_environment,
)
from conda_workspaces.exceptions import EnvironmentNotInstalledError, SolveError
from conda_workspaces.models import (
    Channel,
    Environment,
    Feature,
    MatchSpec,
    PyPIDependency,
    WorkspaceConfig,
)
from conda_workspaces.resolver import ResolvedEnvironment

_real_find_spec = importlib.util.find_spec


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceContext:
    """A WorkspaceContext backed by tmp_path with a simple config."""
    config = WorkspaceConfig(
        name="envs-test",
        root=str(tmp_path),
        channels=[Channel("conda-forge")],
        platforms=["linux-64"],
        features={
            "default": Feature(
                name="default",
                conda_dependencies={"python": MatchSpec("python >=3.10")},
            ),
        },
        environments={"default": Environment(name="default")},
    )
    return WorkspaceContext(config)


def test_remove_environment(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    tmp_workspace_env(workspace.root, "default")
    assert workspace.env_exists("default")

    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)
    remove_environment(workspace, "default")
    assert not workspace.env_exists("default")


def test_remove_environment_nonexistent(
    workspace: WorkspaceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing a non-existent env is a no-op (no error)."""
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)
    remove_environment(workspace, "nonexistent")


def test_clean_all(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    tmp_workspace_env(workspace.root, "default")
    tmp_workspace_env(workspace.root, "test")
    assert workspace.envs_dir.is_dir()

    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)
    clean_all(workspace)
    assert not workspace.envs_dir.is_dir()


def test_clean_all_no_envs_dir(workspace: WorkspaceContext) -> None:
    """clean_all is a no-op when envs_dir doesn't exist."""
    clean_all(workspace)  # should not raise


def test_clean_all_preserves_non_environment_directories(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    prefix = tmp_workspace_env(workspace.root, "default")
    unrelated = workspace.envs_dir / "not-an-environment"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_bytes(b"keep")
    monkeypatch.setattr("conda_workspaces.envs.unregister_env", lambda path: None)

    clean_all(workspace)

    assert not prefix.exists()
    assert (unrelated / "keep.txt").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "env_names, expected",
    [
        ([], []),
        (["default"], ["default"]),
        (["test", "default", "docs"], ["default", "docs", "test"]),
    ],
    ids=["empty", "single", "sorted-multiple"],
)
def test_list_installed_environments(
    workspace: WorkspaceContext,
    env_names: list[str],
    expected: list[str],
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    for name in env_names:
        tmp_workspace_env(workspace.root, name)
    assert list_installed_environments(workspace) == expected


def test_list_installed_ignores_non_conda_dirs(
    workspace: WorkspaceContext, tmp_workspace_env: CreateWorkspaceEnv
) -> None:
    """Directories without conda-meta should be excluded."""
    tmp_workspace_env(workspace.root, "real")
    (workspace.envs_dir / "not-an-env").mkdir(parents=True)
    assert list_installed_environments(workspace) == ["real"]


def test_list_installed_no_envs_dir(workspace: WorkspaceContext) -> None:
    assert list_installed_environments(workspace) == []


@pytest.mark.parametrize(
    "installed, pkg_count, expect_packages",
    [
        (False, 0, False),
        (True, 0, True),
        (True, 5, True),
    ],
    ids=["not-installed", "installed-empty", "installed-with-pkgs"],
)
def test_get_environment_info(
    workspace: WorkspaceContext,
    installed: bool,
    pkg_count: int,
    expect_packages: bool,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    if installed:
        tmp_workspace_env(workspace.root, "default", pkg_count=pkg_count)

    info = get_environment_info(workspace, "default")
    assert info["name"] == "default"
    assert info["exists"] is installed

    if expect_packages:
        assert info["packages"] == pkg_count
    else:
        assert "packages" not in info


@dataclass
class FakeTransaction:
    """Stand-in for UnlinkLinkTransaction."""

    nothing_to_do: bool = False
    summary_printed: bool = field(default=False, init=False)
    downloaded: bool = field(default=False, init=False)
    executed: bool = field(default=False, init=False)

    def print_transaction_summary(self) -> None:
        self.summary_printed = True

    def download_and_extract(self) -> None:
        self.downloaded = True

    def execute(self) -> None:
        self.executed = True


@dataclass
class FakeSolver:
    """Stand-in for the solver backend."""

    prefix: str = ""
    channels: list = field(default_factory=list)
    subdirs: tuple = ()
    specs_to_add: list = field(default_factory=list)
    specs_to_remove: list = field(default_factory=list)
    constructor_kwargs: dict = field(default_factory=dict)
    txn: FakeTransaction = field(default_factory=FakeTransaction)
    solve_kwargs: dict = field(default_factory=dict)

    def solve_for_transaction(self, **kwargs) -> FakeTransaction:
        self.solve_kwargs = kwargs
        return self.txn


def _stub_conda_imports(monkeypatch: pytest.MonkeyPatch, solver: FakeSolver) -> None:
    """Patch all conda imports inside install_environment."""

    class FakePluginManager:
        def get_cached_solver_backend(self):
            def factory(
                prefix,
                channels,
                subdirs,
                specs_to_add=(),
                specs_to_remove=(),
                **kw,
            ):
                solver.prefix = prefix
                solver.channels = channels
                solver.subdirs = subdirs
                solver.specs_to_add = list(specs_to_add)
                solver.specs_to_remove = list(specs_to_remove)
                solver.constructor_kwargs = kw
                return solver

            return factory

    class FakeContext:
        plugin_manager = FakePluginManager()
        subdirs = ("linux-64", "noarch")

    monkeypatch.setattr(envs_mod, "conda_context", FakeContext())


def test_install_empty_specs(workspace: WorkspaceContext) -> None:
    """With no dependencies, install creates an empty conda environment."""
    resolved = ResolvedEnvironment(name="default")
    install_environment(workspace, resolved)
    assert workspace.env_exists("default")


@pytest.mark.parametrize("dry_run", [False, True], ids=["install", "dry-run"])
def test_install_empty_specs_activation(
    workspace: WorkspaceContext,
    snapshot_tree: SnapshotTree,
    dry_run: bool,
) -> None:
    script = workspace.root / "activate.sh"
    script.write_text("export PREVIEW=1\n", encoding="utf-8")
    resolved = ResolvedEnvironment(
        name="default",
        activation_env={"PREVIEW": "1"},
        activation_scripts=[str(script)],
    )
    before = snapshot_tree(workspace.root)

    install_environment(workspace, resolved, dry_run=dry_run)

    prefix = workspace.env_prefix("default")
    if dry_run:
        assert snapshot_tree(workspace.root) == before
        assert not prefix.exists()
    else:
        assert workspace.env_exists("default")
        assert PrefixData(str(prefix)).get_environment_env_vars() == {"PREVIEW": "1"}
        assert (prefix / "etc" / "conda" / "activate.d" / script.name).is_file()


def test_install_dry_run_rejects_invalid_prefix_without_writes(
    workspace: WorkspaceContext,
    snapshot_tree: SnapshotTree,
) -> None:
    prefix = workspace.env_prefix("default")
    prefix.parent.mkdir(parents=True)
    prefix.write_bytes(b"keep")
    before = snapshot_tree(workspace.root)

    with pytest.raises(FileExistsError):
        install_environment(
            workspace,
            ResolvedEnvironment(name="default"),
            dry_run=True,
        )

    assert snapshot_tree(workspace.root) == before


@pytest.mark.parametrize(
    "nothing_to_do, dry_run, expect_summary, expect_downloaded, expect_executed",
    [
        (False, False, False, True, True),
        (False, True, True, False, False),
        (True, False, False, False, False),
    ],
    ids=["new-env", "dry-run", "nothing-to-do"],
)
def test_install_transaction_outcomes(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    nothing_to_do: bool,
    dry_run: bool,
    expect_summary: bool,
    expect_downloaded: bool,
    expect_executed: bool,
) -> None:
    txn = FakeTransaction(nothing_to_do=nothing_to_do)
    solver = FakeSolver(txn=txn)
    _stub_conda_imports(monkeypatch, solver)

    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
    )
    install_environment(workspace, resolved, dry_run=dry_run)

    assert txn.summary_printed is expect_summary
    assert txn.downloaded is expect_downloaded
    assert txn.executed is expect_executed


def test_install_dry_run_isolates_package_cache(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
) -> None:
    configured_cache = workspace.root / "configured-pkgs"
    configured_cache.mkdir()
    (configured_cache / "keep.txt").write_bytes(b"keep")
    solver_caches: list[tuple[Path, ...]] = []

    class CacheWritingSolver(FakeSolver):
        def solve_for_transaction(self, **kwargs) -> FakeTransaction:
            caches = tuple(Path(path) for path in conda_context.pkgs_dirs)
            cache = caches[0]
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "solver-write.txt").write_bytes(b"solver")
            solver_caches.append(caches)
            return super().solve_for_transaction(**kwargs)

    solver = CacheWritingSolver()
    _stub_conda_imports(monkeypatch, solver)
    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
    )

    with conda_context._override("_pkgs_dirs", (str(configured_cache),)):
        before = snapshot_tree(configured_cache)
        install_environment(workspace, resolved, dry_run=True)
        assert snapshot_tree(configured_cache) == before
        assert conda_context.pkgs_dirs == (str(configured_cache),)

    assert len(solver_caches) == 1
    assert solver_caches[0][1:] == (configured_cache,)
    assert not solver_caches[0][0].exists()


@pytest.mark.parametrize("dry_run", [False, True], ids=["replace", "dry-run"])
def test_install_force_reinstall(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    snapshot_tree: SnapshotTree,
    dry_run: bool,
) -> None:
    prefix = tmp_workspace_env(workspace.root, "default")
    (prefix / "keep.txt").write_bytes(b"keep me")
    txn = FakeTransaction()
    solver = FakeSolver(txn=txn)
    _stub_conda_imports(monkeypatch, solver)
    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
    )
    preview_prefix = prefix.with_name(".default.dry-run")
    if dry_run:
        preview_prefix.symlink_to(prefix.with_name("missing"))
    before = snapshot_tree(workspace.root)

    install_environment(
        workspace,
        resolved,
        force_reinstall=True,
        dry_run=dry_run,
    )

    assert solver.solve_kwargs == {}
    if dry_run:
        assert snapshot_tree(workspace.root) == before
        assert solver.prefix == str(prefix.with_name(".default.dry-run-1"))
        assert txn.summary_printed
        assert not txn.downloaded
        assert not txn.executed
    else:
        assert not prefix.exists()
        assert solver.prefix == str(prefix)
        assert not txn.summary_printed
        assert txn.downloaded
        assert txn.executed


def test_install_nothing_to_do_dry_run_skips_activation_writes(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    snapshot_tree: SnapshotTree,
) -> None:
    tmp_workspace_env(workspace.root, "default")
    script = workspace.root / "activate.sh"
    script.write_text("export PREVIEW=1\n", encoding="utf-8")
    txn = FakeTransaction(nothing_to_do=True)
    solver = FakeSolver(txn=txn)
    _stub_conda_imports(monkeypatch, solver)
    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
        activation_env={"PREVIEW": "1"},
        activation_scripts=[str(script)],
    )
    before = snapshot_tree(workspace.root)

    install_environment(workspace, resolved, dry_run=True)

    assert snapshot_tree(workspace.root) == before


def test_install_existing_env_uses_freeze(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    """Updating an existing env passes UpdateModifier.FREEZE_INSTALLED."""
    tmp_workspace_env(workspace.root, "default")

    recorded_kwargs: dict = {}

    class RecordingSolver:
        def __init__(self, prefix, channels, subdirs, specs_to_add=(), **kw):
            pass

        def solve_for_transaction(self, **kwargs):
            recorded_kwargs.update(kwargs)
            return FakeTransaction()

    class FakePluginManager:
        def get_cached_solver_backend(self):
            return RecordingSolver

    class FakeContext:
        plugin_manager = FakePluginManager()
        subdirs = ("linux-64", "noarch")

    monkeypatch.setattr(envs_mod, "conda_context", FakeContext())

    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
    )
    install_environment(workspace, resolved)

    assert recorded_kwargs.get("update_modifier") is UpdateModifier.FREEZE_INSTALLED


def test_install_selective_update_uses_only_requested_constrained_roots(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    """Selective updates leave every unrequested direct root frozen."""
    prefix = tmp_workspace_env(workspace.root, "default")
    PrefixData(str(prefix)).insert(
        PrefixRecord(
            name="python",
            version="3.10.0",
            build="0",
            build_number=0,
            channel="conda-forge",
            subdir="linux-64",
            fn="python-3.10.0-0.conda",
            url=(
                "https://conda.anaconda.org/conda-forge/linux-64/python-3.10.0-0.conda"
            ),
            depends=[],
        )
    )
    solver = FakeSolver()
    _stub_conda_imports(monkeypatch, solver)
    python_spec = MatchSpec(
        name="python",
        version=">=3.10",
        channel="conda-forge",
        subdir="linux-64",
        build="0",
    )
    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={
            "numpy": MatchSpec("numpy <2"),
            "python": python_spec,
        },
        channels=[Channel("conda-forge")],
    )

    install_environment(workspace, resolved, update_names={"python"})

    assert solver.specs_to_add == [python_spec]
    assert solver.constructor_kwargs == {"command": "update"}
    assert solver.solve_kwargs == {"update_modifier": UpdateModifier.FREEZE_INSTALLED}


def test_install_normal_update_keeps_full_manifest_request(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    """The existing non-selective install path keeps its solver contract."""
    tmp_workspace_env(workspace.root, "default")
    solver = FakeSolver()
    _stub_conda_imports(monkeypatch, solver)
    python_spec = MatchSpec(
        name="python",
        version=">=3.10",
        channel="conda-forge",
        subdir="linux-64",
        build="0",
    )
    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={
            "numpy": MatchSpec("numpy <2"),
            "python": python_spec,
        },
        channels=[Channel("conda-forge")],
    )

    install_environment(workspace, resolved)

    assert solver.specs_to_add == list(resolved.conda_dependencies.values())
    assert solver.constructor_kwargs == {}
    assert solver.solve_kwargs == {"update_modifier": UpdateModifier.FREEZE_INSTALLED}


def test_install_selective_update_requires_installed_environment(
    workspace: WorkspaceContext,
) -> None:
    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
    )

    with pytest.raises(EnvironmentNotInstalledError, match="default"):
        install_environment(workspace, resolved, update_names={"python"})


@pytest.mark.parametrize(
    ("update_names", "error", "match"),
    [
        pytest.param(
            {"numpy"},
            ValueError,
            "undeclared conda dependencies: numpy",
            id="undeclared-root",
        ),
        pytest.param(
            {"python"},
            PackageNotInstalledError,
            "python",
            id="uninstalled-root",
        ),
    ],
)
def test_install_selective_update_rejects_missing_roots(
    workspace: WorkspaceContext,
    tmp_workspace_env: CreateWorkspaceEnv,
    update_names: set[str],
    error: type[Exception],
    match: str,
) -> None:
    tmp_workspace_env(workspace.root, "default")
    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
    )

    with pytest.raises(error, match=match):
        install_environment(workspace, resolved, update_names=update_names)


@pytest.mark.parametrize(
    ("has_remaining_spec", "expected_removed"),
    [
        (True, {"boltons"}),
        (False, {"boltons", "python"}),
    ],
    ids=["remaining-spec", "empty-environment"],
)
@pytest.mark.parametrize("dry_run", [False, True], ids=["install", "dry-run"])
def test_install_prune_reconciles_requested_specs(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    has_remaining_spec: bool,
    expected_removed: set[str],
    dry_run: bool,
) -> None:
    """Pruning removes stale requests before installing the remaining specs."""
    tmp_workspace_env(workspace.root, "default")
    solver_calls: list[FakeSolver] = []

    class FakePluginManager:
        def get_cached_solver_backend(self):
            def factory(
                prefix,
                channels,
                subdirs,
                specs_to_add=(),
                specs_to_remove=(),
                **kw,
            ):
                solver = FakeSolver(
                    prefix=prefix,
                    channels=list(channels),
                    subdirs=tuple(subdirs),
                    specs_to_add=list(specs_to_add),
                    specs_to_remove=list(specs_to_remove),
                )
                solver_calls.append(solver)
                return solver

            return factory

    class FakeContext:
        plugin_manager = FakePluginManager()
        subdirs = ("linux-64", "noarch")

    monkeypatch.setattr(
        envs_mod.History,
        "get_requested_specs_map",
        lambda self: {
            "python": MatchSpec("python >=3.10"),
            "boltons": MatchSpec("boltons"),
        },
    )
    monkeypatch.setattr(envs_mod, "conda_context", FakeContext())

    dependencies = {"python": MatchSpec("python >=3.10")} if has_remaining_spec else {}
    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies=dependencies,
        channels=[Channel("conda-forge")],
    )
    install_environment(workspace, resolved, dry_run=dry_run, prune=True)

    expected_call_count = 2 if has_remaining_spec and not dry_run else 1
    assert len(solver_calls) == expected_call_count
    if dry_run and has_remaining_spec:
        assert {spec.name for spec in solver_calls[0].specs_to_add} == {"python"}
        assert solver_calls[0].specs_to_remove == []
        assert solver_calls[0].solve_kwargs == {
            "update_modifier": UpdateModifier.UPDATE_SPECS,
            "prune": True,
        }
    else:
        assert {
            spec.name for spec in solver_calls[0].specs_to_remove
        } == expected_removed
        assert solver_calls[0].specs_to_add == []
        assert solver_calls[0].solve_kwargs == {
            "update_modifier": UpdateModifier.UPDATE_SPECS
        }
    if has_remaining_spec and not dry_run:
        assert {spec.name for spec in solver_calls[1].specs_to_add} == {"python"}
        assert solver_calls[1].specs_to_remove == []
        assert solver_calls[1].solve_kwargs == {
            "update_modifier": UpdateModifier.FREEZE_INSTALLED
        }

    assert all(solver.txn.summary_printed is dry_run for solver in solver_calls)
    assert all(solver.txn.executed is not dry_run for solver in solver_calls)


def test_install_solve_error(
    workspace: WorkspaceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingSolver:
        def __init__(self, prefix, channels, subdirs, specs_to_add=(), **kw):
            pass

        def solve_for_transaction(self, **kwargs):
            raise UnsatisfiableError({})

    class FakePluginManager:
        def get_cached_solver_backend(self):
            return FailingSolver

    class FakeContext:
        plugin_manager = FakePluginManager()
        subdirs = ("linux-64", "noarch")

    monkeypatch.setattr(envs_mod, "conda_context", FakeContext())

    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
    )
    with pytest.raises(SolveError, match="default"):
        install_environment(workspace, resolved)


def test_install_no_solver_backend(
    workspace: WorkspaceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakePluginManager:
        def get_cached_solver_backend(self):
            return None

    class FakeContext:
        plugin_manager = FakePluginManager()
        subdirs = ("linux-64", "noarch")

    monkeypatch.setattr(envs_mod, "conda_context", FakeContext())

    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
    )
    with pytest.raises(SolveError, match="No solver backend"):
        install_environment(workspace, resolved)


@pytest.fixture
def fake_pypi_translate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake ``conda_pypi.translate`` module into sys.modules."""
    conda_pypi = types.ModuleType("conda_pypi")
    tr_mod = types.ModuleType("conda_pypi.translate")
    tr_mod.pypi_to_conda_name = lambda n: n.replace("-", "_")

    monkeypatch.setitem(sys.modules, "conda_pypi", conda_pypi)
    monkeypatch.setitem(sys.modules, "conda_pypi.translate", tr_mod)


@pytest.fixture
def block_conda_pypi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make all ``conda_pypi.*`` imports raise ImportError."""
    real_import = builtins.__import__

    def _block(name, *args, **kwargs):
        if name.startswith("conda_pypi"):
            raise ImportError("no conda-pypi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block)


@pytest.mark.parametrize(
    "dep, expected_name",
    [
        (
            {"name": "my-package", "spec": ">=2.0"},
            "my_package",
        ),
        (
            {"name": "requests", "spec": ">=2.28", "extras": ("security", "socks")},
            "requests",
        ),
    ],
    ids=["translates-names", "handles-extras"],
)
def test_build_pypi_specs(
    fake_pypi_translate: None,
    dep: dict,
    expected_name: str,
) -> None:
    """_build_pypi_specs translates names and builds valid MatchSpecs."""
    pypi_dep = PyPIDependency(**dep)
    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={dep["name"]: pypi_dep},
    )
    specs = _build_pypi_specs(resolved)

    assert len(specs) == 1
    assert specs[0].name == expected_name


def test_build_pypi_specs_skips_path_deps(
    fake_pypi_translate: None,
) -> None:
    """Path/git/url deps are excluded (handled by _install_path_deps)."""
    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={
            "local-pkg": PyPIDependency(name="local-pkg", path="./local"),
            "git-pkg": PyPIDependency(
                name="git-pkg", git="https://example.com/repo.git"
            ),
            "url-pkg": PyPIDependency(
                name="url-pkg", url="https://example.com/pkg.whl"
            ),
            "normal": PyPIDependency(name="normal", spec=">=1.0"),
        },
    )
    specs = _build_pypi_specs(resolved)

    assert len(specs) == 1
    assert specs[0].name == "normal"


@pytest.mark.parametrize(
    "pypi_deps",
    [
        {},
        {
            "local": {"name": "local", "path": "./src"},
            "vcs": {"name": "vcs", "git": "https://example.com/repo.git"},
        },
    ],
    ids=["empty-deps", "all-path-deps"],
)
def test_build_pypi_specs_returns_empty(pypi_deps: dict) -> None:
    """Returns [] without importing conda-pypi when no indexable deps exist."""
    deps = {k: PyPIDependency(**v) for k, v in pypi_deps.items()}
    resolved = ResolvedEnvironment(name="default", pypi_dependencies=deps)
    assert _build_pypi_specs(resolved) == []


def test_build_pypi_specs_no_conda_pypi(
    block_conda_pypi: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Returns empty list and warns when conda-pypi is not installed."""
    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={
            "requests": PyPIDependency(name="requests"),
        },
    )

    with caplog.at_level(logging.WARNING):
        specs = _build_pypi_specs(resolved)

    assert specs == []
    assert "conda-pypi is not installed" in caplog.text


def test_build_pypi_specs_no_rattler_solver(
    fake_pypi_translate: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Warns when conda-pypi is available but conda-rattler-solver is not."""
    monkeypatch.setattr(
        "importlib.util.find_spec",
        lambda name: None if name == "conda_rattler_solver" else _real_find_spec(name),
    )
    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={
            "requests": PyPIDependency(name="requests"),
        },
    )

    with caplog.at_level(logging.WARNING):
        specs = _build_pypi_specs(resolved)

    assert len(specs) == 1
    assert "conda-rattler-solver is not installed" in caplog.text


def test_install_path_deps_no_conda_pypi(
    block_conda_pypi: None,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Warns and skips when conda-pypi is not installed."""
    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={
            "local": PyPIDependency(name="local", path="./src", editable=True),
        },
    )

    with caplog.at_level(logging.WARNING):
        _install_path_deps(tmp_path, resolved)

    assert "conda-pypi is not installed" in caplog.text


def test_install_path_deps_skips_normal(tmp_path: Path) -> None:
    """Normal (non-path/git/url) deps are not processed."""
    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={
            "requests": PyPIDependency(name="requests", spec=">=2.28"),
        },
    )
    _install_path_deps(tmp_path, resolved)


def test_install_path_deps_warns_git_url(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Git and URL deps are warned about and skipped."""
    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={
            "vcs": PyPIDependency(name="vcs", git="https://example.com/repo.git"),
            "remote": PyPIDependency(name="remote", url="https://example.com/pkg.whl"),
        },
    )

    with caplog.at_level(logging.WARNING):
        _install_path_deps(tmp_path, resolved)

    assert "not yet supported" in caplog.text


@pytest.fixture
def fake_pypi_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Inject fake ``conda_pypi.build`` and ``conda_pypi.installer`` modules.

    Returns ``(build_calls, install_calls)`` lists that record invocations.
    The default ``pypa_to_conda`` succeeds; override ``build_mod.pypa_to_conda``
    via monkeypatch to simulate failures.
    """
    build_calls: list[dict] = []
    install_calls: list = []

    sentinel_package = tmp_path / "built.conda"
    sentinel_package.touch()

    def fake_pypa_to_conda(project, **kwargs):
        build_calls.append({"project": project, **kwargs})
        return sentinel_package

    def fake_install_ephemeral(prefix, package):
        install_calls.append(package)

    conda_pypi = types.ModuleType("conda_pypi")
    build_mod = types.ModuleType("conda_pypi.build")
    build_mod.pypa_to_conda = fake_pypa_to_conda
    inst_mod = types.ModuleType("conda_pypi.installer")
    inst_mod.install_ephemeral_conda = fake_install_ephemeral

    monkeypatch.setitem(sys.modules, "conda_pypi", conda_pypi)
    monkeypatch.setitem(sys.modules, "conda_pypi.build", build_mod)
    monkeypatch.setitem(sys.modules, "conda_pypi.installer", inst_mod)

    return build_calls, install_calls, build_mod, sentinel_package


def test_install_path_deps_success(
    fake_pypi_build: tuple,
    tmp_path: Path,
) -> None:
    """When conda-pypi is available, builds and installs path deps."""
    build_calls, install_calls, _, sentinel_package = fake_pypi_build

    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={
            "local": PyPIDependency(name="local", path="./src", editable=True),
        },
    )
    _install_path_deps(tmp_path, resolved)

    assert len(build_calls) == 1
    assert build_calls[0]["distribution"] == "editable"
    assert len(install_calls) == 1
    assert install_calls[0] == sentinel_package


def test_install_path_deps_build_failure_warns(
    fake_pypi_build: tuple,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When pypa_to_conda raises, logs warning and continues."""
    _, _, build_mod, _ = fake_pypi_build

    def broken_build(project, **kwargs):
        raise RuntimeError("build exploded")

    build_mod.pypa_to_conda = broken_build
    monkeypatch.setitem(sys.modules, "conda_pypi.build", build_mod)

    resolved = ResolvedEnvironment(
        name="default",
        pypi_dependencies={
            "broken": PyPIDependency(name="broken", path="./broken"),
        },
    )

    with caplog.at_level(logging.WARNING):
        _install_path_deps(tmp_path, resolved)

    assert "Failed to install" in caplog.text
    assert "broken" in caplog.text


def test_install_merges_pypi_specs_into_solver(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    fake_pypi_translate: None,
) -> None:
    """PyPI deps are translated and passed to the solver alongside conda deps."""
    txn = FakeTransaction()
    solver = FakeSolver(txn=txn)
    _stub_conda_imports(monkeypatch, solver)

    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        pypi_dependencies={
            "my-lib": PyPIDependency(name="my-lib", spec=">=1.0"),
        },
        channels=[Channel("conda-forge")],
    )
    install_environment(workspace, resolved)

    spec_names = {s.name for s in solver.specs_to_add}
    assert "python" in spec_names
    assert "my_lib" in spec_names


def test_apply_activation_env(
    workspace: WorkspaceContext,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    """activation_env vars are written to the prefix state file."""
    prefix = tmp_workspace_env(workspace.root, "default")

    _apply_activation_env(prefix, {"MY_VAR": "hello", "OTHER": "world"})

    pd = PrefixData(str(prefix))
    env_vars = pd.get_environment_env_vars()
    assert env_vars["MY_VAR"] == "hello"
    assert env_vars["OTHER"] == "world"


def test_apply_activation_env_empty(
    workspace: WorkspaceContext,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    """Empty env dict is a no-op."""
    prefix = tmp_workspace_env(workspace.root, "default")
    _apply_activation_env(prefix, {})


def test_apply_activation_scripts(
    workspace: WorkspaceContext,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    """Activation scripts are copied to $PREFIX/etc/conda/activate.d/."""
    prefix = tmp_workspace_env(workspace.root, "default")

    script = workspace.root / "setup.sh"
    script.write_text("#!/bin/sh\nexport FOO=bar\n", encoding="utf-8")

    _apply_activation_scripts(prefix, [str(script)])

    dest = prefix / "etc" / "conda" / "activate.d" / "setup.sh"
    assert dest.exists()
    assert "FOO=bar" in dest.read_text(encoding="utf-8")


def test_apply_activation_scripts_missing(
    workspace: WorkspaceContext,
    tmp_workspace_env: CreateWorkspaceEnv,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing scripts are skipped with a warning."""
    prefix = tmp_workspace_env(workspace.root, "default")

    with caplog.at_level(logging.WARNING):
        _apply_activation_scripts(prefix, ["/nonexistent/script.sh"])

    assert "skipping" in caplog.text


def test_apply_activation_scripts_empty(
    workspace: WorkspaceContext,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    """Empty scripts list is a no-op."""
    prefix = tmp_workspace_env(workspace.root, "default")
    _apply_activation_scripts(prefix, [])
    assert not (prefix / "etc" / "conda" / "activate.d").exists()


@pytest.mark.parametrize(
    "sys_reqs, initial_specs, expected_names",
    [
        (
            {"glibc": "2.17", "cuda": "12.0"},
            [MatchSpec("python >=3.10")],
            {"python", "__glibc", "__cuda"},
        ),
        (
            {"__linux": "5.15"},
            [],
            {"__linux"},
        ),
    ],
    ids=["auto-prefix", "already-prefixed"],
)
def test_apply_system_requirements(
    sys_reqs: dict[str, str],
    initial_specs: list[MatchSpec],
    expected_names: set[str],
) -> None:
    """system_requirements adds virtual package specs without double-prefixing."""
    resolved = ResolvedEnvironment(name="test", system_requirements=sys_reqs)
    result = _apply_system_requirements(resolved, initial_specs)

    assert {str(s.name) for s in result} == expected_names


def test_channel_priority_override() -> None:
    """_channel_priority_override temporarily changes context.channel_priority."""
    original = conda_context.channel_priority

    with _channel_priority_override("strict"):
        assert conda_context.channel_priority == ChannelPriority.STRICT

    assert conda_context.channel_priority == original


def test_channel_priority_override_none() -> None:
    """None priority is a no-op."""
    original = conda_context.channel_priority
    with _channel_priority_override(None):
        assert conda_context.channel_priority == original


def test_install_applies_activation_after_transaction(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    """Activation env vars and scripts are applied post-install."""
    # Pre-create the prefix so FakeTransaction.execute() has conda-meta/ in place
    tmp_workspace_env(workspace.root, "default")

    txn = FakeTransaction()
    solver = FakeSolver(txn=txn)
    _stub_conda_imports(monkeypatch, solver)

    script = workspace.root / "activate.sh"
    script.write_text("#!/bin/sh\nexport PROJ=1\n", encoding="utf-8")

    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
        activation_env={"PROJECT_ROOT": str(workspace.root)},
        activation_scripts=[str(script)],
    )
    install_environment(workspace, resolved)

    assert txn.executed

    prefix = workspace.env_prefix("default")
    pd = PrefixData(str(prefix))
    env_vars = pd.get_environment_env_vars()
    assert env_vars["PROJECT_ROOT"] == str(workspace.root)

    activate_d = prefix / "etc" / "conda" / "activate.d" / "activate.sh"
    assert activate_d.exists()


def test_install_applies_channel_priority(
    workspace: WorkspaceContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install_environment respects channel_priority from the manifest."""
    recorded_priority: list[ChannelPriority] = []

    class PriorityRecordingSolver:
        def __init__(self, prefix, channels, subdirs, specs_to_add=(), **kw):
            recorded_priority.append(conda_context.channel_priority)

        def solve_for_transaction(self, **kwargs):
            return FakeTransaction()

    class FakePluginManager:
        def get_cached_solver_backend(self):
            return PriorityRecordingSolver

    class FakeContext:
        plugin_manager = FakePluginManager()
        subdirs = ("linux-64", "noarch")
        channel_priority = ChannelPriority.FLEXIBLE

        def _override(self, key, value):
            return conda_context._override(key, value)

    monkeypatch.setattr(envs_mod, "conda_context", FakeContext())

    resolved = ResolvedEnvironment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        channels=[Channel("conda-forge")],
        channel_priority="strict",
    )
    install_environment(workspace, resolved)

    assert recorded_priority[0] == ChannelPriority.STRICT
