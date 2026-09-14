"""Shared test fixtures for conda-workspaces."""

from __future__ import annotations

pytest_plugins = ["conda.testing", "conda.testing.fixtures"]

import json
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

import conda_workspaces.publication as publication_mod
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.manifests import detect_and_parse
from conda_workspaces.models import (
    Channel,
    Environment,
    Feature,
    MatchSpec,
    Task,
    TaskDependency,
    TaskOverride,
    WorkspaceConfig,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from conda.testing.fixtures import TmpEnvFixture


class CreateWorkspaceEnv(Protocol):
    """Callable signature for the tmp_workspace_env factory."""

    def __call__(self, workspace: Path, name: str, *, pkg_count: int = 0) -> Path: ...


class ExistingExtractTarget(Protocol):
    """Callable signature for creating pre-existing extraction targets."""

    def __call__(self, kind: str, *, name: str = "extracted") -> Path: ...


class SnapshotTree(Protocol):
    """Callable signature for byte-for-byte filesystem snapshots."""

    def __call__(self, root: Path) -> dict[str, tuple[str, bytes | str | None]]: ...


class ReplacePublicationWriter(Protocol):
    """Callable signature for replacing both publication writer variants."""

    def __call__(
        self,
        callback: Callable[[Path, str, Callable[[str], None]], None],
    ) -> None: ...


@pytest.fixture
def image_workspace(
    tmp_path: Path,
) -> Callable[..., tuple[WorkspaceConfig, WorkspaceContext]]:
    """Create a real manifest and empty exact lock without solving or downloading."""

    def create(
        *,
        manifest_extra: str = "",
        files: dict[str, str] | None = None,
        platforms: tuple[str, ...] = ("linux-64",),
    ) -> tuple[WorkspaceConfig, WorkspaceContext]:
        root = tmp_path / "workspace"
        root.mkdir()
        manifest = root / "conda.toml"
        manifest.write_text(
            '[workspace]\nname = "image-test"\nchannels = []\n'
            f"platforms = {json.dumps(platforms)}\n" + manifest_extra,
            encoding="utf-8",
        )
        (root / "app.py").write_text('print("hello")\n', encoding="utf-8")
        for name, content in (files or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        data = {
            "version": 1,
            "environments": {
                "default": {
                    "channels": [],
                    "packages": {platform: [] for platform in platforms},
                }
            },
            "packages": [],
        }
        (root / "conda.lock").write_text(json.dumps(data), encoding="utf-8")
        _, config = detect_and_parse(manifest)
        return config, WorkspaceContext(config)

    return create


@pytest.fixture
def record_image_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., list[list[str]]]:
    """Replace Docker only, recording argv and writing simulated build artifacts."""
    original_run = subprocess.run
    original_which = shutil.which

    def record(
        *,
        fail_operation: str | None = None,
        on_build: Callable[[list[str]], None] | None = None,
    ) -> list[list[str]]:
        calls: list[list[str]] = []

        def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[:2] != ["docker", "buildx"]:
                return original_run(args, **kwargs)
            calls.append(list(args))
            assert not kwargs.get("shell")
            assert kwargs["check"] is True
            assert kwargs["stderr"] is sys.stderr
            operation = args[2]
            if operation == "build":
                assert kwargs["stdout"] is sys.stderr
                metadata = Path(args[args.index("--metadata-file") + 1])
                metadata.write_text(
                    json.dumps({"containerimage.digest": "sha256:" + "a" * 64}),
                    encoding="utf-8",
                )
                if "--output" in args:
                    output = args[args.index("--output") + 1]
                    Path(output.split("dest=", 1)[1]).write_bytes(b"OCI image archive")
                if on_build is not None:
                    on_build(args)
            if operation == fail_operation:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout="")

        def which(command: str, **kwargs: object) -> str | None:
            return (
                "/usr/bin/docker"
                if command == "docker"
                else original_which(command, **kwargs)
            )

        monkeypatch.setattr("conda_workspaces.image.subprocess.run", run)
        monkeypatch.setattr("conda_workspaces.image.shutil.which", which)
        return calls

    return record


@pytest.fixture
def replace_lockfile_install_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., None]:
    """Replace one module's prepared lockfile installer with a recording fake."""

    def replace(
        module: str,
        record: Callable[..., None],
        *,
        preflight_prefix_identity: tuple[int, int] | None = None,
    ) -> None:
        identity = preflight_prefix_identity

        class FakeInstallPlan:
            preflight_prefix_identity = identity

            def __init__(
                self,
                ctx: object,
                name: str,
                kwargs: dict[str, object],
            ) -> None:
                self.ctx = ctx
                self.name = name
                self.kwargs = kwargs

            @classmethod
            def prepare(
                cls,
                ctx: object,
                name: str,
                **kwargs: object,
            ) -> FakeInstallPlan:
                record("prepare", ctx, name, kwargs)
                return cls(ctx, name, kwargs)

            def execute(self) -> None:
                record("execute", self.ctx, self.name, self.kwargs)

            def remove_preflight_prefix(self, ctx: object) -> None:
                if self.preflight_prefix_identity is not None:
                    record(
                        "remove",
                        ctx,
                        self.name,
                        {"expected_prefix_identity": self.preflight_prefix_identity},
                    )

        monkeypatch.setattr(f"{module}.LockfileInstallPlan", FakeInstallPlan)

    return replace


@pytest.fixture
def replace_publication_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> ReplacePublicationWriter:
    """Replace path and descriptor-relative publication with one callback."""
    original_write = publication_mod.atomic_write_text
    original_write_at = publication_mod.atomic_write_text_at

    def replace(
        callback: Callable[[Path, str, Callable[[str], None]], None],
    ) -> None:
        def write(path: Path, content: str, **kwargs: object) -> None:
            callback(
                path,
                content,
                lambda replacement: original_write(path, replacement, **kwargs),
            )

        def write_at(
            directory_descriptor: int,
            name: str,
            content: str,
            *,
            display_path: Path,
            **kwargs: object,
        ) -> None:
            callback(
                display_path,
                content,
                lambda replacement: original_write_at(
                    directory_descriptor,
                    name,
                    replacement,
                    display_path=display_path,
                    **kwargs,
                ),
            )

        monkeypatch.setattr(publication_mod, "atomic_write_text", write)
        monkeypatch.setattr(publication_mod, "atomic_write_text_at", write_at)

    return replace


@pytest.fixture
def snapshot_tree() -> SnapshotTree:
    """Return a recursive snapshot function that does not follow symlinks."""

    def _snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
        snapshot: dict[str, tuple[str, bytes | str | None]] = {}
        if not root.exists():
            return snapshot
        snapshot["."] = ("directory", None)
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = ("symlink", str(path.readlink()))
            elif path.is_dir():
                snapshot[relative] = ("directory", None)
            else:
                snapshot[relative] = ("file", path.read_bytes())
        return snapshot

    return _snapshot


@pytest.fixture
def sample_pixi_toml(tmp_path: Path) -> Path:
    """Create a minimal pixi.toml in tmp_path and return its path."""
    content = """\
[workspace]
name = "test-project"
version = "0.1.0"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"
numpy = ">=1.24"

[feature.test.dependencies]
pytest = ">=8.0"

[feature.docs.dependencies]
sphinx = ">=7.0"

[environments]
default = []
test = {features = ["test"]}
docs = {features = ["docs"]}
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def sample_pyproject_toml(tmp_path: Path) -> Path:
    """Create a pyproject.toml with [tool.pixi.*] tables."""
    content = """\
[project]
name = "my-project"
version = "1.0.0"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[tool.pixi.dependencies]
python = ">=3.11"

[tool.pixi.feature.test.dependencies]
pytest = ">=8.0"
pytest-cov = ">=4.0"

[tool.pixi.environments]
test = {features = ["test"]}
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def sample_config() -> WorkspaceConfig:
    """Return a pre-built WorkspaceConfig for unit tests."""
    default_feat = Feature(
        name="default",
        conda_dependencies={
            "python": MatchSpec("python >=3.10"),
            "numpy": MatchSpec("numpy >=1.24"),
        },
    )
    test_feat = Feature(
        name="test",
        conda_dependencies={
            "pytest": MatchSpec("pytest >=8.0"),
        },
    )
    docs_feat = Feature(
        name="docs",
        conda_dependencies={
            "sphinx": MatchSpec("sphinx >=7.0"),
        },
    )

    return WorkspaceConfig(
        name="test-project",
        version="0.1.0",
        channels=[Channel("conda-forge")],
        platforms=["linux-64", "osx-arm64", "win-64"],
        features={
            "default": default_feat,
            "test": test_feat,
            "docs": docs_feat,
        },
        environments={
            "default": Environment(name="default"),
            "test": Environment(name="test", features=["test"]),
            "docs": Environment(name="docs", features=["docs"]),
        },
        root="/tmp/test-project",
        manifest_path="/tmp/test-project/pixi.toml",
    )


@pytest.fixture
def tmp_workspace_env(tmp_env: TmpEnvFixture) -> Iterator[CreateWorkspaceEnv]:
    """Factory fixture: creates a shallow conda environment inside a workspace.

    Delegates to conda's ``tmp_env(shallow=True)`` to create the
    environment at the workspace-relative ``.conda/envs/<name>/`` path.

    Usage: ``prefix = tmp_workspace_env(workspace, "default", pkg_count=3)``
    """
    stack = ExitStack()

    def _create(workspace: Path, name: str, *, pkg_count: int = 0) -> Path:
        prefix = stack.enter_context(
            tmp_env(shallow=True, prefix=workspace / ".conda" / "envs" / name)
        )
        for i in range(pkg_count):
            (prefix / "conda-meta" / f"pkg-{i}.json").write_text("{}", encoding="utf-8")
        return prefix

    with stack:
        yield _create


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A temporary directory acting as a project root."""
    return tmp_path


@pytest.fixture
def existing_extract_target(tmp_path: Path) -> ExistingExtractTarget:
    """Factory fixture for unsafe archive extraction targets."""

    def _create(kind: str, *, name: str = "extracted") -> Path:
        target = tmp_path / name
        if kind == "empty":
            target.mkdir()
        elif kind == "non-empty":
            target.mkdir()
            (target / "conda.toml").write_text("trusted = true\n", encoding="utf-8")
        elif kind == "file-target":
            target.write_text("trusted file\n", encoding="utf-8")
        elif kind == "symlink-target":
            destination = tmp_path / f"{name}-destination"
            destination.mkdir()
            try:
                target.symlink_to(destination, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                pytest.skip(f"symlink target setup unavailable: {exc}")
        else:
            raise AssertionError(f"unknown extraction target setup: {kind}")
        return target

    return _create


@pytest.fixture
def sample_yaml(tmp_project: Path) -> Path:
    """Create a sample conda.toml for task testing (legacy fixture name)."""
    content = """\
[tasks]
lint = "ruff check ."
_setup = "mkdir -p build/"
platform-task = "rm -rf build/"

[tasks.build]
cmd = "make build"
depends-on = ["configure"]
description = "Build the project"
inputs = ["src/**/*.py"]
outputs = ["dist/"]

[tasks.configure]
cmd = "cmake -G Ninja -S . -B .build"
description = "Configure build system"

[tasks.test]
cmd = "pytest {{ test_path }}"
env = { PYTHONPATH = "src" }
clean-env = true
args = [{ arg = "test_path", default = "tests/" }]

[tasks.check]
depends-on = ["test", "lint"]
description = "Run all checks"

[target.win-64.tasks]
platform-task = "rd /s /q build"
"""
    path = tmp_project / "conda.toml"
    path.write_text(content)
    return path


@pytest.fixture
def simple_task() -> Task:
    return Task(name="build", cmd="make build", description="Build it")


@pytest.fixture
def task_with_deps() -> dict[str, Task]:
    return {
        "configure": Task(name="configure", cmd="cmake ."),
        "build": Task(
            name="build",
            cmd="make",
            depends_on=[TaskDependency(task="configure")],
        ),
        "test": Task(
            name="test",
            cmd="pytest",
            depends_on=[TaskDependency(task="build")],
        ),
    }


@pytest.fixture
def task_with_overrides() -> Task:
    return Task(
        name="clean",
        cmd="rm -rf build/",
        platforms={
            "win-64": TaskOverride(cmd="rd /s /q build"),
            "osx-arm64": TaskOverride(env={"MACOSX_DEPLOYMENT_TARGET": "11.0"}),
        },
    )


@pytest.fixture
def alias_task() -> Task:
    return Task(
        name="check",
        depends_on=[
            TaskDependency(task="test"),
            TaskDependency(task="lint"),
        ],
    )
