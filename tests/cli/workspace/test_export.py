"""Tests for ``conda workspace export`` via the plugin exporter hook."""

from __future__ import annotations

import json as json_module
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from conda.common.serialize.yaml import loads as yaml_loads
from rich.console import Console

from conda_workspaces.cli.workspace.export import execute_export
from conda_workspaces.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
    LockfileNotFoundError,
    PlatformError,
)
from conda_workspaces.export import resolve_exporter, run_exporter
from conda_workspaces.resolver import ResolvedEnvironment

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path


_DEFAULTS = {
    "file": None,
    "environment": "default",
    "format": None,
    "export_platforms": None,
    "from_lockfile": False,
    "from_prefix": False,
    "no_builds": False,
    "ignore_channels": False,
    "from_history": False,
    "dry_run": False,
    "json": False,
}


@pytest.fixture
def export_console() -> Console:
    """A Rich Console that writes to a StringIO buffer for assertions."""
    return Console(file=StringIO(), width=200, highlight=False)


@pytest.mark.parametrize(
    ("format_name", "expected_name"),
    [
        ("environment-yaml", "environment-yaml"),
        ("yaml", "environment-yaml"),
        ("environment-json", "environment-json"),
        ("json", "environment-json"),
        ("conda-workspaces-lock-v1", "conda-workspaces-lock-v1"),
        ("workspace-lock", "conda-workspaces-lock-v1"),
    ],
    ids=[
        "yaml-canonical",
        "yaml-alias",
        "json-canonical",
        "json-alias",
        "workspace-lock-canonical",
        "workspace-lock-alias",
    ],
)
def test_resolve_exporter_by_format(format_name: str, expected_name: str) -> None:
    """The plugin registry resolves canonical names and aliases identically."""
    exporter, resolved = resolve_exporter(format_name=format_name, file_path=None)
    assert exporter.name == expected_name
    assert resolved == expected_name


@pytest.mark.parametrize(
    ("filename", "expected_name"),
    [
        ("environment.yaml", "environment-yaml"),
        ("environment.yml", "environment-yaml"),
        ("environment.json", "environment-json"),
        ("conda.lock", "conda-workspaces-lock-v1"),
    ],
    ids=["yaml-default", "yml-default", "json-default", "conda-lock-name"],
)
def test_resolve_exporter_detects_by_filename(
    tmp_path: Path, filename: str, expected_name: str
) -> None:
    path = tmp_path / filename
    exporter, resolved = resolve_exporter(format_name=None, file_path=path)
    assert exporter.name == expected_name
    assert resolved == expected_name


def test_resolve_exporter_unknown_format_raises() -> None:
    from conda.exceptions import CondaValueError

    with pytest.raises(CondaValueError, match="Unknown export format"):
        resolve_exporter(format_name="not-a-real-format", file_path=None)


@pytest.mark.parametrize(
    ("declared", "requested", "fallback", "expected"),
    [
        (("linux-64", "osx-arm64"), (), "linux-64", ("linux-64", "osx-arm64")),
        (("linux-64", "osx-arm64"), ("linux-64",), "linux-64", ("linux-64",)),
        ((), (), "linux-64", ("linux-64",)),
    ],
    ids=["all-declared", "intersect-single", "fallback-when-none-declared"],
)
def test_resolved_environment_target_platforms(
    declared: tuple[str, ...],
    requested: tuple[str, ...],
    fallback: str,
    expected: tuple[str, ...],
) -> None:
    env = ResolvedEnvironment(name="default", platforms=list(declared))
    assert env.target_platforms(requested=requested, fallback=fallback) == expected


def test_resolved_environment_target_platforms_rejects_unknown() -> None:
    env = ResolvedEnvironment(name="default", platforms=["linux-64"])
    with pytest.raises(PlatformError):
        env.target_platforms(requested=("no-such-subdir",), fallback="linux-64")


def test_export_unknown_environment_raises(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(pixi_workspace)
    with pytest.raises(EnvironmentNotFoundError, match="nope"):
        execute_export(make_args(_DEFAULTS, environment="nope"))


def test_export_declared_source_writes_yaml(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    """Default ``environment-yaml`` export round-trips to valid YAML."""
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / "environment.yaml"

    result = execute_export(
        make_args(_DEFAULTS, file=output, export_platforms=["linux-64"]),
        console=export_console,
    )

    assert result == 0
    assert output.is_file()
    data = yaml_loads(output.read_text(encoding="utf-8"))
    assert "dependencies" in data
    conda_deps = [dep for dep in data["dependencies"] if isinstance(dep, str)]
    assert any(dep.startswith("python") for dep in conda_deps)


def test_export_declared_source_writes_json(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    """``--format environment-json`` produces valid JSON."""
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / "environment.json"

    result = execute_export(
        make_args(
            _DEFAULTS,
            file=output,
            format="environment-json",
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    assert result == 0
    data = json_module.loads(output.read_text(encoding="utf-8"))
    assert "dependencies" in data


def test_export_filename_detection_picks_exporter(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    """A `.json` filename selects the JSON exporter without an explicit --format."""
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / "environment.json"

    execute_export(
        make_args(_DEFAULTS, file=output, export_platforms=["linux-64"]),
        console=export_console,
    )

    assert json_module.loads(output.read_text(encoding="utf-8"))


def test_export_dry_run_writes_nothing(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    export_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / "env.yaml"

    result = execute_export(
        make_args(
            _DEFAULTS,
            file=output,
            dry_run=True,
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    assert result == 0
    assert not output.exists()
    assert "dependencies" in capsys.readouterr().out


def test_export_json_flag_emits_structured_result(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    """``--json`` suppresses the free-form status line and emits a JSON payload."""
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / "env.yaml"

    execute_export(
        make_args(
            _DEFAULTS,
            file=output,
            json=True,
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    payload = json_module.loads(export_console.file.getvalue())
    assert payload == {
        "success": True,
        "file": str(output),
        "format": "environment-yaml",
        "environment": "default",
    }


def test_export_multi_platform_with_single_platform_exporter_fails(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YAML/JSON exporters only accept one platform; extra platforms error out."""
    from conda.exceptions import CondaValueError

    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(CondaValueError, match="Multiple platforms"):
        execute_export(
            make_args(
                _DEFAULTS,
                export_platforms=["linux-64", "osx-arm64"],
                format="environment-yaml",
            )
        )


def test_export_workspace_lock_multiplatform(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    """conda-workspaces-lock-v1 accepts multiple platforms via multiplatform_export."""
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / "conda.lock"

    result = execute_export(
        make_args(
            _DEFAULTS,
            file=output,
            format="conda-workspaces-lock-v1",
            export_platforms=["linux-64", "osx-arm64"],
        ),
        console=export_console,
    )

    assert result == 0
    data = yaml_loads(output.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "environments" in data


def test_export_from_lockfile_missing_raises(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requesting ``--from-lockfile`` without a conda.lock surfaces a clear error."""
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(LockfileNotFoundError):
        execute_export(make_args(_DEFAULTS, from_lockfile=True))


def test_export_from_prefix_not_installed_raises(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(pixi_workspace)
    with pytest.raises(EnvironmentNotInstalledError):
        execute_export(make_args(_DEFAULTS, from_prefix=True))


def test_export_from_lockfile_and_from_prefix_are_mutex(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conda.exceptions import CondaValueError

    monkeypatch.chdir(pixi_workspace)
    with pytest.raises(CondaValueError, match="mutually exclusive"):
        execute_export(make_args(_DEFAULTS, from_lockfile=True, from_prefix=True))


def test_run_exporter_prefers_multiplatform() -> None:
    """A fake exporter with ``multiplatform_export`` receives the full list."""
    calls: list = []

    class FakeExporter:
        name = "fake"
        export = None

        def multiplatform_export(self, envs):
            calls.append(list(envs))
            return "MULTI"

    content = run_exporter(FakeExporter(), ["a", "b"])  # type: ignore[list-item]
    assert content == "MULTI\n"
    assert calls == [["a", "b"]]


def test_run_exporter_falls_back_to_single() -> None:
    """Single-platform exporters receive only ``envs[0]``; newline normalised."""

    class FakeExporter:
        name = "fake"
        multiplatform_export = None

        def export(self, env):
            return env + "no-trailing-newline"

    content = run_exporter(FakeExporter(), ["a"])  # type: ignore[list-item]
    assert content == "ano-trailing-newline\n"
