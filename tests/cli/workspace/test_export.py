"""Tests for ``conda workspace export`` via the plugin exporter hook."""

from __future__ import annotations

import json as json_module
from io import StringIO
from typing import TYPE_CHECKING

import pytest
import tomlkit
from conda.common.serialize.yaml import loads as yaml_loads
from conda.exceptions import CondaValueError
from rich.console import Console

import conda_workspaces.cli.workspace.export as export_module
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
    "manifest_file": None,
    "output": None,
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
        ("conda.toml", "conda-toml"),
        ("pixi.toml", "pixi-toml"),
        ("pyproject.toml", "pyproject-toml"),
    ],
    ids=[
        "yaml-default",
        "yml-default",
        "json-default",
        "conda-lock-name",
        "conda-toml-name",
        "pixi-toml-name",
        "pyproject-toml-name",
    ],
)
def test_resolve_exporter_detects_by_filename(
    tmp_path: Path, filename: str, expected_name: str
) -> None:
    path = tmp_path / filename
    exporter, resolved = resolve_exporter(format_name=None, file_path=path)
    assert exporter.name == expected_name
    assert resolved == expected_name


def test_resolve_exporter_unknown_format_raises() -> None:
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
        make_args(_DEFAULTS, output=output, export_platforms=["linux-64"]),
        console=export_console,
    )

    assert result == 0
    assert output.is_file()
    data = yaml_loads(output.read_text(encoding="utf-8"))
    assert "dependencies" in data
    conda_deps = [dep for dep in data["dependencies"] if isinstance(dep, str)]
    assert any(dep.startswith("python") for dep in conda_deps)


def test_export_status_escapes_repository_controlled_markup(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
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
    output = pixi_workspace / "[blue]environment.yml"

    result = execute_export(
        make_args(
            _DEFAULTS,
            environment="[red]unsafe",
            output=output,
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    assert result == 0
    rendered = export_console.file.getvalue()
    assert "[red]unsafe" in rendered
    assert "[blue]environment.yml" in rendered


def test_export_declared_source_redacts_relative_channel_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    (tmp_path / "conda.toml").write_text(
        """\
[workspace]
channels = ["t/SENSITIVE-VALUE/private"]
platforms = ["linux-64"]
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "environment.yml"

    execute_export(
        make_args(_DEFAULTS, output=output, export_platforms=["linux-64"]),
        console=export_console,
    )

    data = yaml_loads(output.read_text(encoding="utf-8"))
    assert data["channels"] == ["https://conda.anaconda.org/private"]


@pytest.mark.parametrize(
    ("format_name", "filename"),
    [
        ("environment-yaml", "environment.yml"),
        ("environment-json", "environment.json"),
        ("conda-toml", "exported.conda.toml"),
    ],
    ids=["yaml", "json", "conda-toml"],
)
@pytest.mark.parametrize(
    "dependency",
    [
        (
            'artifact = { url = "https://user:LEAKME@packages.example.test/'
            'linux-64/artifact-1.0-0.conda?token=SECRET" }'
        ),
        'artifact = { build = "https://user:LEAKME@packages.example.test/x" }',
    ],
    ids=["url", "build"],
)
def test_export_declared_source_rejects_conda_url_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
    format_name: str,
    filename: str,
    dependency: str,
) -> None:
    (tmp_path / "conda.toml").write_text(
        '[workspace]\nchannels = ["conda-forge"]\nplatforms = ["linux-64"]\n\n'
        f"[dependencies]\n{dependency}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / filename

    with pytest.raises(CondaValueError) as error:
        execute_export(
            make_args(
                _DEFAULTS,
                output=output,
                format=format_name,
                export_platforms=["linux-64"],
            ),
            console=export_console,
        )

    message = str(error.value)
    assert "LEAKME" not in message
    assert "user" not in message
    assert "token=SECRET" not in message
    assert not output.exists()


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
            output=output,
            format="environment-json",
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    assert result == 0
    data = json_module.loads(output.read_text(encoding="utf-8"))
    assert "dependencies" in data


def test_export_uses_exact_manifest_and_separate_output(
    tmp_path: Path,
    export_console: Console,
) -> None:
    manifest = (
        '[workspace]\nname = "{name}"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n[dependencies]\n{name} = "*"\n'
    )
    (tmp_path / "conda.toml").write_text(
        manifest.format(name="conda-only"),
        encoding="utf-8",
    )
    pixi = tmp_path / "pixi.toml"
    pixi.write_text(
        manifest.format(name="pixi-only"),
        encoding="utf-8",
    )
    output = tmp_path / "exports" / "environment.yml"

    execute_export(
        make_args(
            _DEFAULTS,
            manifest_file=pixi,
            output=output,
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    data = yaml_loads(output.read_text(encoding="utf-8"))
    assert "pixi-only" in data["dependencies"]
    assert "conda-only" not in data["dependencies"]


def test_export_filename_detection_picks_exporter(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    """A `.json` filename selects the JSON exporter without an explicit --format."""
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / "environment.json"

    execute_export(
        make_args(_DEFAULTS, output=output, export_platforms=["linux-64"]),
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
            output=output,
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
            output=output,
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
            output=output,
            format="conda-workspaces-lock-v1",
            export_platforms=["linux-64", "osx-arm64"],
        ),
        console=export_console,
    )

    assert result == 0
    data = yaml_loads(output.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "environments" in data


def test_export_workspace_lock_rich_platform_uses_conda_subdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    (tmp_path / "pixi.toml").write_text(
        """\
[workspace]
name = "rich-platform-export"
channels = ["conda-forge"]
platforms = [
  { name = "linux-64-cuda", platform = "linux-64", cuda = "12.0" },
]
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "conda.lock"

    result = execute_export(
        make_args(
            _DEFAULTS,
            output=output,
            format="conda-workspaces-lock-v1",
            export_platforms=["linux-64-cuda"],
        ),
        console=export_console,
    )

    assert result == 0
    data = yaml_loads(output.read_text(encoding="utf-8"))
    assert data["environments"]["default"]["packages"] == {"linux-64": []}


def test_export_from_lockfile_missing_raises(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requesting ``--from-lockfile`` without a conda.lock surfaces a clear error."""
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(LockfileNotFoundError):
        execute_export(make_args(_DEFAULTS, from_lockfile=True))


def test_export_manifest_format_from_lockfile_keeps_exact_package(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    url = "https://conda.anaconda.org/conda-forge/linux-64/python-3.12.0-h123_0.conda"
    digest = "a" * 64
    (pixi_workspace / "conda.lock").write_text(
        "version: 1\n"
        "environments:\n"
        "  default:\n"
        "    channels:\n"
        "    - url: https://conda.anaconda.org/conda-forge\n"
        "    packages:\n"
        "      linux-64:\n"
        f"      - conda: {url}\n"
        "packages:\n"
        f"- conda: {url}\n"
        f"  sha256: {digest}\n",
        encoding="utf-8",
    )

    class FakeRecord:
        name = "python"
        sha256 = digest

        def __init__(self, package_url: str) -> None:
            self.url = package_url

    monkeypatch.setattr(
        "conda_lockfiles.rattler_lock.v6.records_from_conda_urls",
        lambda metadata_by_url, **kwargs: tuple(
            FakeRecord(package_url) for package_url in metadata_by_url
        ),
    )
    output = pixi_workspace / "exported.toml"

    execute_export(
        make_args(
            _DEFAULTS,
            output=output,
            format="conda-toml",
            from_lockfile=True,
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    package = tomlkit.loads(output.read_text(encoding="utf-8")).unwrap()[
        "dependencies"
    ]["python"]
    assert package["url"] == url
    assert package["sha256"] == digest


def test_export_from_prefix_not_installed_raises(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(pixi_workspace)
    with pytest.raises(EnvironmentNotInstalledError):
        execute_export(make_args(_DEFAULTS, from_prefix=True))


def test_export_from_lockfile_and_from_prefix_are_mutex(
    pixi_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


@pytest.mark.parametrize(
    ("format_name", "filename", "top_table", "path"),
    [
        ("conda-toml", "out-conda.toml", "workspace", ("workspace",)),
        ("pixi-toml", "out-pixi.toml", "workspace", ("workspace",)),
        (
            "pyproject-toml",
            "out-pyproject.toml",
            "tool",
            ("tool", "conda", "workspace"),
        ),
    ],
    ids=["conda-toml", "pixi-toml", "pyproject-toml"],
)
def test_export_manifest_format_plugin_hook(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
    format_name: str,
    filename: str,
    top_table: str,
    path: tuple[str, ...],
) -> None:
    """The three manifest exporters are reachable via ``--format`` end-to-end.

    Exercises the full plugin-hook path: ``execute_export`` →
    ``resolve_exporter`` (looks up the new ``CondaEnvironmentExporter``
    in the plugin registry) → ``ManifestParser.export`` (the writer
    registered as ``multiplatform_export``).  Drives all three
    targets through a single parametrised test because the CLI
    contract is identical modulo where the ``[workspace]`` table
    lands in the output document.
    """
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / filename

    result = execute_export(
        make_args(
            _DEFAULTS,
            output=output,
            format=format_name,
            export_platforms=["linux-64", "osx-arm64"],
        ),
        console=export_console,
    )

    assert result == 0
    data = tomlkit.loads(output.read_text(encoding="utf-8")).unwrap()
    assert top_table in data

    cursor: object = data
    for key in path:
        cursor = cursor[key]  # type: ignore[index]
    assert cursor["platforms"] == ["linux-64", "osx-arm64"]


@pytest.mark.parametrize(
    ("format_name", "filename", "dependency_path"),
    [
        ("conda-toml", "exported-conda.toml", ("dependencies", "python")),
        ("pixi-toml", "exported-pixi.toml", ("dependencies", "python")),
        (
            "pyproject-toml",
            "exported-pyproject.toml",
            ("tool", "conda", "dependencies", "python"),
        ),
    ],
    ids=["conda-toml", "pixi-toml", "pyproject-toml"],
)
def test_export_manifest_formats_preserve_channel_qualified_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
    format_name: str,
    filename: str,
    dependency_path: tuple[str, ...],
) -> None:
    (tmp_path / "conda.toml").write_text(
        """\
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = { version = "3.12", channel = "conda-forge" }
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / filename

    execute_export(
        make_args(
            _DEFAULTS,
            output=output,
            format=format_name,
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    cursor: object = tomlkit.loads(output.read_text(encoding="utf-8")).unwrap()
    for key in dependency_path:
        cursor = cursor[key]  # type: ignore[index]
    assert cursor["channel"] == (  # type: ignore[index]
        "https://conda.anaconda.org/conda-forge"
    )


def test_export_rejects_unrepresentable_pypi_source_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    (tmp_path / "conda.toml").write_text(
        """\
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[pypi-dependencies.local]
path = "./local"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "exported.toml"

    with pytest.raises(CondaValueError, match="cannot represent losslessly"):
        execute_export(
            make_args(
                _DEFAULTS,
                output=output,
                format="conda-toml",
                export_platforms=["linux-64"],
            ),
            console=export_console,
        )

    assert not output.exists()


def test_export_rejects_symlinked_output(
    pixi_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    outside = tmp_path / "outside.yml"
    outside.write_text("keep me", encoding="utf-8")
    output = pixi_workspace / "environment.yml"
    output.symlink_to(outside)

    with pytest.raises(ValueError, match="symbolic link"):
        execute_export(
            make_args(
                _DEFAULTS,
                output=output,
                export_platforms=["linux-64"],
            ),
            console=export_console,
        )

    assert outside.read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize(
    ("format_name", "filename"),
    [
        ("environment-yaml", "environment.yml"),
        ("pyproject-toml", "pyproject.toml"),
    ],
    ids=["replace", "merge"],
)
def test_export_rejects_output_changed_during_rendering(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
    format_name: str,
    filename: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    output = pixi_workspace / filename
    output.write_text("# original\n", encoding="utf-8")
    concurrent = "# concurrent\n"
    run_exporter = export_module.run_exporter

    def replace_output(*args, **kwargs):
        content = run_exporter(*args, **kwargs)
        output.write_text(concurrent, encoding="utf-8")
        return content

    monkeypatch.setattr(export_module, "run_exporter", replace_output)

    with pytest.raises(ValueError, match="changed before writing"):
        execute_export(
            make_args(
                _DEFAULTS,
                output=output,
                format=format_name,
                export_platforms=["linux-64"],
            ),
            console=export_console,
        )

    assert output.read_text(encoding="utf-8") == concurrent


def test_export_pyproject_merges_into_existing_file(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_console: Console,
) -> None:
    """``--format pyproject-toml --file pyproject.toml`` preserves peer tables.

    Regression guard for #41: a naive exporter that just
    ``Path.write_text``s the plugin output would destroy
    ``[project]`` / ``[build-system]`` / ``[tool.ruff]`` and any
    other section in an existing ``pyproject.toml``.
    :meth:`PyprojectTomlParser.merge_export`, wired in
    :func:`execute_export`, splices the exporter's ``[tool.conda]``
    subtree in instead.
    """
    monkeypatch.chdir(pixi_workspace)
    pyproject = pixi_workspace / "pyproject.toml"
    pyproject.write_text(
        """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "my-pkg"
version = "0.1.0"

[tool.ruff]
line-length = 120
""",
        encoding="utf-8",
    )

    result = execute_export(
        make_args(
            _DEFAULTS,
            output=pyproject,
            format="pyproject-toml",
            export_platforms=["linux-64"],
        ),
        console=export_console,
    )

    assert result == 0
    data = tomlkit.loads(pyproject.read_text(encoding="utf-8")).unwrap()
    assert data["build-system"]["build-backend"] == "hatchling.build"
    assert data["project"] == {"name": "my-pkg", "version": "0.1.0"}
    assert data["tool"]["ruff"] == {"line-length": 120}
    assert data["tool"]["conda"]["workspace"]["platforms"] == ["linux-64"]
