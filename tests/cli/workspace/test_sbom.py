"""Tests for ``conda workspace sbom``."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections.abc import Callable
from io import StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from conda.base.context import context as conda_context
from conda.core.prefix_data import PrefixData
from conda.exceptions import CondaValueError
from conda.history import History
from conda.models.match_spec import MatchSpec
from conda.models.records import PrefixRecord
from rich.console import Console

import conda_workspaces.cli.workspace.sbom as sbom_module
from conda_workspaces.cli.workspace.sbom import execute_sbom

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path


_METADATA_DEFAULTS = {
    "product_name": None,
    "product_version": None,
    "product_manufacturer": None,
    "product_manufacturer_url": None,
    "author_name": None,
    "author_email": None,
    "author_organization": None,
    "author_organization_url": None,
}

_DEFAULTS = {
    "manifest_file": None,
    "environment": "default",
    "platform": None,
    "from_prefix": False,
    "reproducible": False,
    "from_history": False,
    "output": None,
    "dry_run": False,
    "json": False,
    **_METADATA_DEFAULTS,
}

_ExportCall = tuple[
    argparse.Namespace,
    Console | None,
    Callable[..., str] | None,
    bool,
    bool,
]


@pytest.fixture
def recorded_export_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[_ExportCall]:
    """Record calls delegated to the workspace export command."""
    calls: list[_ExportCall] = []

    def execute_export(
        args: argparse.Namespace,
        *,
        console: Console | None = None,
        export_environment: Callable[..., str] | None = None,
        include_requested_packages: bool = False,
        host_prefix_only: bool = False,
    ) -> int:
        calls.append(
            (
                args,
                console,
                export_environment,
                include_requested_packages,
                host_prefix_only,
            )
        )
        return 17

    monkeypatch.setattr(sbom_module, "execute_export", execute_export)
    return calls


@pytest.fixture
def conda_sboms_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, SimpleNamespace]:
    """Provide only the optional conda-sboms modules requested by a test."""
    modules: dict[str, SimpleNamespace] = {}

    def fake_import_module(name: str) -> SimpleNamespace:
        try:
            return modules[name]
        except KeyError as exc:
            raise ImportError(name) from exc

    monkeypatch.setattr(sbom_module, "import_module", fake_import_module)
    return modules


@pytest.mark.parametrize(
    (
        "from_prefix",
        "platform",
        "expected_platform",
        "expected_from_history",
    ),
    [
        (False, "linux-64", "linux-64", False),
        (True, None, conda_context.subdir, True),
    ],
    ids=["lockfile", "prefix"],
)
def test_sbom_normalizes_and_delegates_export(
    conda_sboms_modules: dict[str, SimpleNamespace],
    recorded_export_calls: list[_ExportCall],
    from_prefix: bool,
    platform: str | None,
    expected_platform: str,
    expected_from_history: bool,
) -> None:
    conda_sboms_modules["conda_sboms.cyclonedx"] = SimpleNamespace(
        FORMAT="cyclonedx-json-v1.7",
        export_cyclonedx_json=lambda environment: str(environment),
    )
    console = Console(file=StringIO(), highlight=False)

    result = execute_sbom(
        make_args(
            _DEFAULTS,
            environment="runtime",
            platform=platform,
            from_prefix=from_prefix,
            output="runtime.cdx.json",
        ),
        console=console,
    )

    assert result == 17
    assert len(recorded_export_calls) == 1
    (
        normalized,
        delegated_console,
        export_environment,
        requested_packages,
        host_prefix_only,
    ) = recorded_export_calls[0]
    assert delegated_console is console
    assert export_environment is not None
    assert requested_packages is True
    assert host_prefix_only is True
    assert normalized.environment == "runtime"
    assert normalized.format == "cyclonedx-json-v1.7"
    assert normalized.export_platforms == [expected_platform]
    assert normalized.from_lockfile == (not from_prefix)
    assert normalized.from_prefix is from_prefix
    assert normalized.from_history is expected_from_history
    assert normalized.no_builds is False
    assert normalized.ignore_channels is False
    assert normalized.output == "runtime.cdx.json"


@pytest.mark.parametrize(
    ("values", "reproducible"),
    [
        ({}, False),
        (
            {
                "product_name": "Acme Runtime",
                "product_version": "2026.08",
                "product_manufacturer": "Acme GmbH",
                "product_manufacturer_url": "https://acme.example",
                "author_name": "Alice Example",
                "author_email": "alice@acme.example",
                "author_organization": "Acme Product Security",
                "author_organization_url": "https://acme.example/security",
            },
            False,
        ),
        ({"author_name": "Alice Example"}, False),
        ({}, True),
        (
            {"product_name": "Acme Runtime", "product_version": "2026.08"},
            True,
        ),
    ],
    ids=[
        "no-options",
        "all-metadata",
        "partial-metadata",
        "reproducible-no-metadata",
        "reproducible-explicit-metadata",
    ],
)
def test_sbom_options_build_one_export_callback(
    conda_sboms_modules: dict[str, SimpleNamespace],
    recorded_export_calls: list[_ExportCall],
    values: dict[str, str],
    reproducible: bool,
) -> None:
    metadata_values: list[dict[str, str | None]] = []
    metadata_instances: list[object] = []
    exporter_calls: list[tuple[object, object | None, bool]] = []

    class FakeMetadata:
        def __init__(self, **values: str | None) -> None:
            metadata_values.append(values)
            metadata_instances.append(self)

    def export_cyclonedx_json(
        environment: object,
        *,
        metadata: object | None = None,
        output_reproducible: bool = False,
    ) -> str:
        exporter_calls.append((environment, metadata, output_reproducible))
        return "generated SBOM\n"

    conda_sboms_modules["conda_sboms.cyclonedx"] = SimpleNamespace(
        FORMAT="cyclonedx-json-v1.7",
        export_cyclonedx_json=export_cyclonedx_json,
    )
    conda_sboms_modules["conda_sboms.settings"] = SimpleNamespace(
        CycloneDXExportMetadata=FakeMetadata
    )

    assert execute_sbom(make_args(_DEFAULTS, reproducible=reproducible, **values)) == 17
    expected_metadata_values = [{**_METADATA_DEFAULTS, **values}] if values else []
    assert metadata_values == expected_metadata_values
    assert len(recorded_export_calls) == 1
    _, _, callback, include_requested_packages, host_prefix_only = (
        recorded_export_calls[0]
    )
    assert include_requested_packages is True
    assert host_prefix_only is True
    assert callback is not None
    environment = object()
    assert callback(environment) == "generated SBOM\n"
    metadata = metadata_instances[0] if values else None
    assert exporter_calls == [(environment, metadata, reproducible)]


@pytest.mark.parametrize(
    ("values", "cyclonedx_available"),
    [
        ({}, False),
        (
            {"product_name": "Acme Runtime", "product_version": "2026.08"},
            True,
        ),
    ],
    ids=["missing-cyclonedx", "missing-settings"],
)
def test_sbom_requires_conda_sboms_0_3_0(
    conda_sboms_modules: dict[str, SimpleNamespace],
    recorded_export_calls: list[_ExportCall],
    values: dict[str, object],
    cyclonedx_available: bool,
) -> None:
    if cyclonedx_available:
        conda_sboms_modules["conda_sboms.cyclonedx"] = SimpleNamespace(
            FORMAT="cyclonedx-json-v1.7",
            export_cyclonedx_json=lambda environment, **kwargs: str(environment),
        )

    with pytest.raises(CondaValueError, match=r"conda-sboms >=0\.3\.0") as error:
        execute_sbom(make_args(_DEFAULTS, **values))

    assert 'conda install -n base "conda-forge::conda-sboms>=0.3.0"' in str(error.value)
    assert recorded_export_calls == []


@pytest.mark.parametrize(
    ("file_output", "dry_run", "result_key"),
    [
        (False, False, "content"),
        (True, False, "file"),
        (True, True, "content"),
    ],
    ids=["stdout", "file", "file-dry-run"],
)
def test_sbom_json_output(
    exact_lockfile_export: tuple[Path, str, str, list[dict[str, object]]],
    capsys: pytest.CaptureFixture[str],
    file_output: bool,
    dry_run: bool,
    result_key: str,
) -> None:
    workspace, _, _, _ = exact_lockfile_export
    output = workspace / "result.cdx.json" if file_output else None
    json_output = StringIO()

    assert (
        execute_sbom(
            make_args(
                _DEFAULTS,
                platform="linux-64",
                output=output,
                reproducible=True,
                product_name="CLI test workspace",
                product_version="1.0.0",
                dry_run=dry_run,
                json=True,
            ),
            console=Console(file=json_output, highlight=False),
        )
        == 0
    )

    assert capsys.readouterr().out == ""
    payload = json.loads(json_output.getvalue())
    assert set(payload) == {"success", "format", "environment", result_key}
    assert payload["success"] is True
    assert payload["format"] == "cyclonedx-json-v1.7"
    assert payload["environment"] == "default"
    assert (output is not None and output.is_file()) is (file_output and not dry_run)
    if result_key == "file":
        assert payload["file"] == str(output)
    else:
        document = json.loads(payload["content"])
        assert document["specVersion"] == "1.7"
        assert "timestamp" not in document["metadata"]


def test_sbom_from_prefix_uses_installed_records_and_history_roots(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: Callable[..., Path],
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    prefix = tmp_workspace_env(pixi_workspace, "default")
    package_url = (
        "https://conda.anaconda.org/conda-forge/"
        f"{conda_context.subdir}/python-3.12.0-h123_0.conda"
    )
    PrefixData(str(prefix)).insert(
        PrefixRecord(
            name="python",
            version="3.12.0",
            build="h123_0",
            build_number=0,
            channel="https://conda.anaconda.org/conda-forge",
            subdir=conda_context.subdir,
            fn="python-3.12.0-h123_0.conda",
            url=package_url,
            sha256="c" * 64,
            license="BSD-3-Clause",
            depends=[],
        )
    )
    history = History(str(prefix))
    history.init_log_file()
    history.write_specs(update_specs=(MatchSpec("python >=3.10"),))
    output = pixi_workspace / "installed.cdx.json"

    assert (
        execute_sbom(
            make_args(
                _DEFAULTS,
                from_prefix=True,
                output=output,
                reproducible=True,
            ),
            console=rich_console,
        )
        == 0
    )

    document = json.loads(output.read_text(encoding="utf-8"))
    root = document["metadata"]["component"]
    component = document["components"][0]
    dependencies = {
        dependency["ref"]: dependency["dependsOn"]
        for dependency in document["dependencies"]
    }
    assert (component["name"], component["version"]) == ("python", "3.12.0")
    assert f"subdir={conda_context.subdir}" in component["purl"]
    assert dependencies[root["bom-ref"]] == [component["bom-ref"]]


def test_sbom_from_lockfile_generates_valid_reproducible_cyclonedx(
    exact_lockfile_export: tuple[Path, str, str, list[dict[str, object]]],
    rich_console: Console,
) -> None:
    workspace, _, digest, _ = exact_lockfile_export
    outputs = [workspace / "first.cdx.json", workspace / "second.cdx.json"]
    for output in outputs:
        assert (
            execute_sbom(
                make_args(
                    _DEFAULTS,
                    platform="linux-64",
                    output=output,
                    reproducible=True,
                    product_name="CLI test workspace",
                    product_version="1.0.0",
                ),
                console=rich_console,
            )
            == 0
        )

    output = outputs[0]
    assert output.read_bytes() == outputs[1].read_bytes()
    content = output.read_text(encoding="utf-8")
    document = json.loads(content)
    root = document["metadata"]["component"]
    component = document["components"][0]

    assert document["specVersion"] == "1.7"
    assert "timestamp" not in document["metadata"]
    assert {item["name"]: item["value"] for item in document["metadata"]["properties"]}[
        "cdx:reproducible"
    ] == "true"
    assert (root["name"], root["version"]) == ("CLI test workspace", "1.0.0")
    assert component["purl"] == (
        "pkg:conda/python@3.12.0?build=h123_0&channel=conda-forge"
        "&subdir=linux-64&type=conda"
    )
    assert component["hashes"] == [{"alg": "SHA-256", "content": digest}]
    assert component["licenses"] == [{"license": {"name": "BSD-3-Clause"}}]
    dependencies = {
        dependency["ref"]: dependency["dependsOn"]
        for dependency in document["dependencies"]
    }
    assert dependencies[root["bom-ref"]] == [component["bom-ref"]]

    cyclonedx_cli = shutil.which("cyclonedx-cli")
    trivy = shutil.which("trivy")
    assert cyclonedx_cli is not None
    assert trivy is not None
    subprocess.run(
        [
            cyclonedx_cli,
            "validate",
            "--input-file",
            str(output),
            "--input-format",
            "json",
            "--input-version",
            "v1_7",
            "--fail-on-errors",
        ],
        check=True,
    )
    completed = subprocess.run(
        [
            trivy,
            "sbom",
            "--scanners",
            "license",
            "--offline-scan",
            "--format",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    trivy_report = json.loads(completed.stdout)
    assert trivy_report["SchemaVersion"] == 2
    assert trivy_report["ArtifactType"] == "cyclonedx"
    conda_packages = next(
        result["Packages"]
        for result in trivy_report["Results"]
        if result.get("Class") == "lang-pkgs" and result.get("Type") == "conda-pkg"
    )
    python_package = next(
        package for package in conda_packages if package["Name"] == "python"
    )
    assert python_package["Identifier"]["PURL"] == component["purl"]
    assert "BSD-3-Clause" in python_package["Licenses"]
