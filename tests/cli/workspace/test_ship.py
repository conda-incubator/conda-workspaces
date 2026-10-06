"""Tests for building native launchers from locked workspace environments."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.main import execute_workspace, generate_workspace_parser
from conda_workspaces.cli.workspace.ship import execute_ship
from conda_workspaces.exceptions import CondaWorkspacesError

if TYPE_CHECKING:
    from collections.abc import Callable

    from conda_workspaces.context import WorkspaceContext
    from conda_workspaces.models import WorkspaceConfig

    from ...conftest import SnapshotTree


def test_ship_parser_selects_locked_environment() -> None:
    args = generate_workspace_parser().parse_args(
        ["ship", "-e", "runtime", "--platform", "linux-64", "-o", "dist"]
    )
    assert args.environment == "runtime"
    assert args.platform == "linux-64"
    assert args.output == Path("dist")
    assert args.delegate_executable is None
    assert args.artifact_layout is None


@pytest.mark.parametrize("missing", ["environment", "platform", "output"])
def test_ship_requires_explicit_selection(missing: str) -> None:
    options = {"environment": "runtime", "platform": "linux-64", "output": "dist"}
    options.pop(missing)
    with pytest.raises(SystemExit) as exc:
        generate_workspace_parser().parse_args(
            [
                "ship",
                *(arg for key, value in options.items() for arg in (f"--{key}", value)),
            ]
        )
    assert exc.value.code == 2


@pytest.mark.parametrize("layout", [None, "online", "external", "embedded"], ids=str)
@pytest.mark.parametrize("dry_run", [False, True], ids=["build", "preview"])
@pytest.mark.parametrize("json_output", [False, True], ids=["human", "json"])
def test_ship_delegates_without_solving_or_changing_workspace(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    snapshot_tree: SnapshotTree,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    layout: str | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    config, ctx = image_workspace()
    before = snapshot_tree(ctx.root)
    calls = record_ship_builder()

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Shipping must not solve or access a workspace environment prefix")

    monkeypatch.setattr(
        "conda_workspaces.resolver.ResolvedEnvironment.solve_for_platform", unexpected
    )
    monkeypatch.setattr(
        "conda_workspaces.context.WorkspaceContext.env_prefix", unexpected
    )
    options = []
    if layout:
        options.extend(["--artifact-layout", layout])
    if dry_run:
        options.append("--dry-run")
    if json_output:
        options.append("--json")
    output = ctx.root / "dist with spaces"
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            config.manifest_path,
            "ship",
            "-e",
            "default",
            "--platform",
            "linux-64",
            "-o",
            str(output),
            "--delegate-executable",
            "python",
            *options,
        ]
    )

    assert execute_workspace(args) == 0
    assert len(calls) == 2
    assert calls[1][4:] == [
        "--manifest",
        config.manifest_path,
        "--source-lock",
        str(ctx.root / "conda.lock"),
        "--source-environment",
        "default",
        "--platform",
        "linux-64",
        "--out-dir",
        str(output),
        "--delegate-executable",
        "python",
        *(["--artifact-layout", layout] if layout else []),
        *(["--dry-run"] if dry_run else []),
    ]
    assert snapshot_tree(ctx.root) == before
    captured = capsys.readouterr()
    assert "conda-ship build output" in captured.err
    if json_output:
        assert json.loads(captured.out) == {
            "success": True,
            "environment": "default",
            "platform": "linux-64",
            "output": str(output),
            "dry_run": dry_run,
        }
    else:
        assert "launcher" in captured.out


@pytest.mark.parametrize("filename", ["conda.toml", "pixi.toml", "pyproject.toml"])
def test_ship_selects_exact_manifest_and_environment(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    filename: str,
) -> None:
    _, ctx = image_workspace(
        manifest_extra="\n[environments]\ndefault = []\nruntime = []\n"
    )
    original = ctx.root / "conda.toml"
    manifest = ctx.root / filename
    text = original.read_text()
    if filename == "pyproject.toml":
        text = text.replace("[workspace]", "[tool.conda.workspace]").replace(
            "[environments]", "[tool.conda.environments]"
        )
    if manifest != original:
        original.write_text("invalid autodetected manifest")
    manifest.write_text(text + '\n[tool.conda-ship]\nsource-environment = "default"\n')
    lock = ctx.root / "conda.lock"
    data = json.loads(lock.read_text())
    data["environments"]["runtime"] = data["environments"]["default"]
    lock.write_text(json.dumps(data))
    calls = record_ship_builder()
    before = (manifest.read_bytes(), lock.read_bytes())
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            str(manifest),
            "ship",
            "-e",
            "runtime",
            "--platform",
            "linux-64",
            "-o",
            str(ctx.root / "dist"),
        ]
    )
    assert execute_ship(args) == 0
    build = calls[1]
    assert build[build.index("--manifest") + 1] == str(manifest)
    assert build[build.index("--source-lock") + 1] == str(lock)
    assert build[build.index("--source-environment") + 1] == "runtime"
    assert "--delegate-executable" not in build
    assert (manifest.read_bytes(), lock.read_bytes()) == before


@pytest.mark.parametrize(
    "problem",
    ["missing", "stale", "malformed", "version", "platform", "environment", "package"],
)
def test_ship_rejects_invalid_locks_before_running_builder(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    snapshot_tree: SnapshotTree,
    problem: str,
) -> None:
    extra = '\n[dependencies]\npython = "*"\n' if problem == "stale" else ""
    config, ctx = image_workspace(manifest_extra=extra)
    lock = ctx.root / "conda.lock"
    data = json.loads(lock.read_text())
    if problem == "missing":
        lock.unlink()
    elif problem == "malformed":
        lock.write_text("[")
    else:
        if problem == "version":
            data["version"] = 99
        elif problem == "platform":
            data["environments"]["default"]["packages"] = {}
        elif problem == "environment":
            data["environments"] = {}
        elif problem == "package":
            data["environments"]["default"]["packages"]["linux-64"] = [
                {"conda": "missing"}
            ]
        lock.write_text(json.dumps(data))
    calls = record_ship_builder()
    before = snapshot_tree(ctx.root)
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            config.manifest_path,
            "ship",
            "-e",
            "default",
            "--platform",
            "linux-64",
            "-o",
            str(ctx.root / "dist"),
        ]
    )
    with pytest.raises(CondaWorkspacesError):
        execute_ship(args)
    assert calls == []
    assert snapshot_tree(ctx.root) == before


@pytest.mark.parametrize(
    "settings, message, expected_calls",
    [
        ({"available": False}, "Install", 0),
        ({"help_text": "--root"}, "input selection", 1),
        ({"execution_error": OSError("cannot execute")}, "cannot execute", 1),
    ],
    ids=["missing-package", "incompatible-builder", "execution-error"],
)
def test_ship_reports_unavailable_builder(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    settings: dict[str, object],
    message: str,
    expected_calls: int,
) -> None:
    config, ctx = image_workspace()
    calls = record_ship_builder(**settings)
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            config.manifest_path,
            "ship",
            "-e",
            "default",
            "--platform",
            "linux-64",
            "-o",
            str(ctx.root / "dist"),
        ]
    )
    with pytest.raises(CondaWorkspacesError, match=message):
        execute_ship(args)
    assert len(calls) == expected_calls


def test_ship_preserves_builder_errors_and_exit_code(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, ctx = image_workspace()
    record_ship_builder(exit_code=7)
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            config.manifest_path,
            "ship",
            "-e",
            "default",
            "--platform",
            "linux-64",
            "-o",
            str(ctx.root / "dist"),
            "--json",
        ]
    )
    assert execute_workspace(args) == 7
    captured = capsys.readouterr()
    assert "unsupported dependency" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("requested", ["linux-cuda12", "linux-64"])
def test_ship_rejects_named_platform_variants(
    rich_platform_lockfile: Callable[[dict[str, str], tuple[str, ...]], Path],
    record_ship_builder: Callable[..., list[list[str]]],
    requested: str,
) -> None:
    root = rich_platform_lockfile(
        {"linux-cuda12": "12", "linux-cuda13": "13"}, ("linux-cuda12", "linux-cuda13")
    )
    calls = record_ship_builder()
    args = generate_workspace_parser().parse_args(
        ["ship", "-e", "default", "--platform", requested, "-o", str(root / "dist")]
    )
    with pytest.raises(CondaWorkspacesError, match="named platform"):
        execute_ship(args)
    assert calls == []


@pytest.mark.parametrize(
    "source",
    [
        'path = "."',
        'git = "https://example.com/app.git"',
        'url = "https://example.com/app.whl"',
    ],
)
def test_ship_rejects_unlocked_application_sources(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    source: str,
) -> None:
    config, ctx = image_workspace(
        manifest_extra=f"\n[pypi-dependencies]\napp = {{ {source} }}\n"
    )
    calls = record_ship_builder()
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            config.manifest_path,
            "ship",
            "-e",
            "default",
            "--platform",
            "linux-64",
            "-o",
            str(ctx.root / "dist"),
        ]
    )
    with pytest.raises(CondaWorkspacesError, match="app.*(path|Git|URL)"):
        execute_ship(args)
    assert calls == []


@pytest.mark.parametrize(
    "structure",
    [None, [], {"default": []}, {"default": {"channels": None}}],
    ids=["null-environments", "list-environments", "list-environment", "null-channels"],
)
def test_ship_reports_invalid_lock_structure(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    structure: object,
) -> None:
    config, ctx = image_workspace()
    lock = ctx.root / "conda.lock"
    data = json.loads(lock.read_text())
    data["environments"] = structure
    lock.write_text(json.dumps(data))
    calls = record_ship_builder()
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            config.manifest_path,
            "ship",
            "-e",
            "default",
            "--platform",
            "linux-64",
            "-o",
            str(ctx.root / "dist"),
        ]
    )
    with pytest.raises(CondaWorkspacesError):
        execute_ship(args)
    assert calls == []


def test_ship_requires_pypi_translation_to_check_manifest_requirements(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ctx = image_workspace(
        manifest_extra='\n[pypi-dependencies]\nrequests = ">=2"\n'
    )
    calls = record_ship_builder()

    monkeypatch.setitem(sys.modules, "conda_pypi.translate", None)
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            config.manifest_path,
            "ship",
            "-e",
            "default",
            "--platform",
            "linux-64",
            "-o",
            str(ctx.root / "dist"),
        ]
    )
    with pytest.raises(CondaWorkspacesError, match="conda-pypi"):
        execute_ship(args)
    assert calls == []
