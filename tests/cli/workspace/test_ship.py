"""Tests for building native launchers from locked workspace environments."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conda.base.context import context as conda_context
from conda.base.context import reset_context

from conda_workspaces.cli.main import execute_workspace, generate_workspace_parser
from conda_workspaces.cli.workspace.ship import execute_ship
from conda_workspaces.exceptions import CondaWorkspacesError

if TYPE_CHECKING:
    import argparse
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
def test_ship_delegates_without_solving_or_changing_inputs(
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
    assert len(calls) == 1
    assert calls[0][4:] == [
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
        verb = "Would write" if dry_run else "Wrote"
        assert f"{verb} launcher outputs to" in captured.out


@pytest.mark.usefixtures("reset_conda_context")
def test_ship_builds_with_installed_conda_ship(
    native_ship_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    if sys.version_info < (3, 11):
        pytest.skip("conda-ship requires Python 3.11 or later")
    if conda_context.subdir == "linux-aarch64":
        pytest.skip("conda-forge does not provide conda-ship for linux-aarch64")
    monkeypatch.delenv("CONDA_SHIP_EXECUTABLE", raising=False)
    monkeypatch.delenv("CONDA_SHIP_TEMPLATE", raising=False)
    root = native_ship_workspace
    manifest = root / "conda.toml"
    lock = root / "conda.lock"
    before = manifest.read_bytes(), lock.read_bytes()
    output = root / "dist with spaces"
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            str(manifest),
            "ship",
            "-e",
            "default",
            "--platform",
            conda_context.subdir,
            "-o",
            str(output),
            "--artifact-layout",
            "online",
            "--json",
        ]
    )

    assert execute_workspace(args) == 0

    assert json.loads(capfd.readouterr().out) == {
        "success": True,
        "environment": "default",
        "platform": conda_context.subdir,
        "output": str(output),
        "dry_run": False,
    }
    assert (manifest.read_bytes(), lock.read_bytes()) == before
    executable = "ship-smoke.exe" if sys.platform == "win32" else "ship-smoke"
    assert (output / executable).is_file()
    for suffix in ("info.json", "sha256", "runtime.lock", "packages.txt", "cdx.json"):
        assert (output / f"ship-smoke.{suffix}").is_file()
    info = json.loads((output / "ship-smoke.info.json").read_text())
    assert info["platform"] == conda_context.subdir
    assert info["layout"] == "online"
    assert info["package_count"] == 1


@pytest.mark.usefixtures("reset_conda_context")
@pytest.mark.parametrize(
    ("options", "json_environment"),
    [
        pytest.param(["--json"], None, id="flag"),
        pytest.param([], "true", id="environment"),
        pytest.param(["--console", "json"], None, id="console"),
    ],
)
def test_ship_honors_conda_json_configuration(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    ship_args: Callable[..., argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    json_environment: str | None,
) -> None:
    config, _ = image_workspace()
    record_ship_builder()
    if json_environment is None:
        monkeypatch.delenv("CONDA_JSON", raising=False)
    else:
        monkeypatch.setenv("CONDA_JSON", json_environment)
    args = ship_args(config, *options)
    reset_context(argparse_args=args)

    assert execute_workspace(args) == 0

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["success"] is True
    assert result["environment"] == "default"
    assert result["platform"] == "linux-64"
    assert "conda-ship build output" in captured.err


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
    build = calls[0]
    assert build[build.index("--manifest") + 1] == str(manifest)
    assert build[build.index("--source-lock") + 1] == str(lock)
    assert build[build.index("--source-environment") + 1] == "runtime"
    assert "--delegate-executable" not in build
    assert (manifest.read_bytes(), lock.read_bytes()) == before


@pytest.mark.parametrize("environment", ["default", "outdated"])
def test_ship_checks_only_selected_environment_requirements(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    ship_args: Callable[..., argparse.Namespace],
    environment: str,
) -> None:
    config, ctx = image_workspace(
        manifest_extra=(
            '\n[feature.outdated.dependencies]\npython = "3.13.*"\n'
            '\n[environments]\ndefault = []\noutdated = ["outdated"]\n'
        )
    )
    lock = ctx.root / "conda.lock"
    data = json.loads(lock.read_text())
    data["environments"]["outdated"] = data["environments"]["default"]
    lock.write_text(json.dumps(data))
    calls = record_ship_builder()
    args = ship_args(config)
    args.environment = environment

    if environment == "default":
        assert execute_ship(args) == 0
        assert len(calls) == 1
    else:
        with pytest.raises(CondaWorkspacesError, match="outdated conda.lock"):
            execute_ship(args)
        assert calls == []


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "stale",
        "malformed",
        "version",
        "platform",
        "environment",
        "package",
        "unknown-environment",
        "unsupported-platform",
    ],
)
def test_ship_rejects_invalid_locks_before_running_builder(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    ship_args: Callable[..., argparse.Namespace],
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
    args = ship_args(config)
    if problem == "unknown-environment":
        args.environment = "unknown"
    elif problem == "unsupported-platform":
        args.platform = "osx-64"
    with pytest.raises(CondaWorkspacesError) as exc:
        execute_ship(args)
    if problem == "missing":
        assert "conda workspace lock" in str(exc.value)
        assert "conda workspace install" not in str(exc.value)
    assert calls == []
    assert snapshot_tree(ctx.root) == before


@pytest.mark.parametrize(
    "settings, message, expected_calls",
    [
        ({"available": False}, "Install", 0),
        ({"installed_version": "0.10.0"}, ">=0.11.1", 0),
        ({"installed_version": "0.11.0"}, ">=0.11.1", 0),
        ({"installed_version": "0.11.1rc1"}, ">=0.11.1", 0),
        ({"installed_version": "unknown"}, ">=0.11.1", 0),
        ({"execution_error": OSError("cannot execute")}, "cannot execute", 1),
    ],
    ids=[
        "missing-package",
        "missing-input-selection",
        "missing-installed-executable-fix",
        "prerelease-builder",
        "invalid-version",
        "execution-error",
    ],
)
def test_ship_reports_unavailable_builder(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    ship_args: Callable[..., argparse.Namespace],
    settings: dict[str, object],
    message: str,
    expected_calls: int,
) -> None:
    config, _ = image_workspace()
    calls = record_ship_builder(**settings)
    args = ship_args(config)
    with pytest.raises(CondaWorkspacesError, match=message):
        execute_ship(args)
    assert len(calls) == expected_calls


def test_ship_preserves_builder_errors_and_exit_code(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    ship_args: Callable[..., argparse.Namespace],
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, _ = image_workspace()
    record_ship_builder(exit_code=7)
    args = ship_args(config, "--json")
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
    ship_args: Callable[..., argparse.Namespace],
    source: str,
) -> None:
    config, _ = image_workspace(
        manifest_extra=f"\n[pypi-dependencies]\napp = {{ {source} }}\n"
    )
    calls = record_ship_builder()
    args = ship_args(config)
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
    ship_args: Callable[..., argparse.Namespace],
    structure: object,
) -> None:
    config, ctx = image_workspace()
    lock = ctx.root / "conda.lock"
    data = json.loads(lock.read_text())
    data["environments"] = structure
    lock.write_text(json.dumps(data))
    calls = record_ship_builder()
    args = ship_args(config)
    with pytest.raises(CondaWorkspacesError):
        execute_ship(args)
    assert calls == []


def test_ship_requires_pypi_translation_to_check_manifest_requirements(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_ship_builder: Callable[..., list[list[str]]],
    ship_args: Callable[..., argparse.Namespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ = image_workspace(
        manifest_extra='\n[pypi-dependencies]\nrequests = ">=2"\n'
    )
    calls = record_ship_builder()

    monkeypatch.setitem(sys.modules, "conda_pypi.translate", None)
    args = ship_args(config)
    with pytest.raises(CondaWorkspacesError, match="conda-pypi"):
        execute_ship(args)
    assert calls == []
