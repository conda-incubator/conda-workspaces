"""Tests for ``conda workspace sbom``."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import types
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from conda.base.context import context as conda_context
from conda.exceptions import CondaValueError
from rich.console import Console

import conda_workspaces.cli.workspace.sbom as sbom_module
from conda_workspaces.cli.workspace.sbom import execute_sbom

if TYPE_CHECKING:
    from collections.abc import Callable
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


def sbom_args(**overrides: object) -> argparse.Namespace:
    """Build an SBOM command namespace with parser-equivalent defaults."""
    return argparse.Namespace(**{**_DEFAULTS, **overrides})


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
    monkeypatch: pytest.MonkeyPatch,
    from_prefix: bool,
    platform: str | None,
    expected_platform: str,
    expected_from_history: bool,
) -> None:
    calls: list[
        tuple[argparse.Namespace, Console | None, Callable | None, bool, bool]
    ] = []

    def execute_export(
        args: argparse.Namespace,
        *,
        console: Console | None = None,
        export_environment: Callable | None = None,
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
    console = Console(file=StringIO(), highlight=False)

    result = execute_sbom(
        sbom_args(
            environment="runtime",
            platform=platform,
            from_prefix=from_prefix,
            output="runtime.cdx.json",
        ),
        console=console,
    )

    assert result == 17
    assert len(calls) == 1
    (
        normalized,
        delegated_console,
        export_environment,
        requested_packages,
        host_prefix_only,
    ) = calls[0]
    assert delegated_console is console
    assert export_environment is None
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
        "all-metadata",
        "partial-metadata",
        "reproducible-configured-metadata",
        "reproducible-explicit-metadata",
    ],
)
def test_sbom_options_build_one_export_callback(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
    reproducible: bool,
) -> None:
    metadata_values: list[dict[str, str | None]] = []
    metadata_instances: list[object] = []
    exporter_calls: list[tuple[object, object | None, bool]] = []
    delegated_callbacks: list[Callable | None] = []

    class FakeMetadata:
        def __init__(self, **values: str | None) -> None:
            metadata_values.append(values)
            metadata_instances.append(self)

    class FakeExporter:
        def export(self) -> str:
            return "generated SBOM\n"

    class LegacyExporter(FakeExporter):
        def __init__(self, environment: object, *, metadata: object) -> None:
            exporter_calls.append((environment, metadata, False))

    class CurrentExporter(FakeExporter):
        def __init__(
            self,
            environment: object,
            *,
            metadata: object | None = None,
            output_reproducible: bool = False,
        ) -> None:
            exporter_calls.append((environment, metadata, output_reproducible))

    package = types.ModuleType("conda_sboms")
    package.__path__ = []  # type: ignore[attr-defined]
    cyclonedx = types.ModuleType("conda_sboms.cyclonedx")
    cyclonedx.CycloneDXExporter = (  # type: ignore[attr-defined]
        CurrentExporter if reproducible else LegacyExporter
    )
    settings = types.ModuleType("conda_sboms.settings")
    settings.CycloneDXExportMetadata = FakeMetadata  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "conda_sboms", package)
    monkeypatch.setitem(sys.modules, "conda_sboms.cyclonedx", cyclonedx)
    monkeypatch.setitem(sys.modules, "conda_sboms.settings", settings)

    def execute_export(
        args: argparse.Namespace,
        *,
        console: Console | None = None,
        export_environment: Callable | None = None,
        include_requested_packages: bool = False,
        host_prefix_only: bool = False,
    ) -> int:
        del args, console
        assert include_requested_packages is True
        assert host_prefix_only is True
        delegated_callbacks.append(export_environment)
        return 0

    monkeypatch.setattr(sbom_module, "execute_export", execute_export)
    assert execute_sbom(sbom_args(reproducible=reproducible, **values)) == 0
    expected_metadata_values = [{**_METADATA_DEFAULTS, **values}] if values else []
    assert metadata_values == expected_metadata_values
    assert len(delegated_callbacks) == 1
    callback = delegated_callbacks[0]
    assert callback is not None
    environment = object()
    assert callback(environment) == "generated SBOM\n"
    metadata = metadata_instances[0] if values else None
    assert exporter_calls == [(environment, metadata, reproducible)]


@pytest.mark.parametrize("api", ["missing", "old"], ids=["missing", "old"])
def test_sbom_metadata_requires_current_conda_sboms_api(
    monkeypatch: pytest.MonkeyPatch,
    api: str,
) -> None:
    if api == "missing":
        monkeypatch.setitem(sys.modules, "conda_sboms", None)
        monkeypatch.delitem(sys.modules, "conda_sboms.cyclonedx", raising=False)
        monkeypatch.delitem(sys.modules, "conda_sboms.settings", raising=False)
    else:
        package = types.ModuleType("conda_sboms")
        package.__path__ = []  # type: ignore[attr-defined]
        cyclonedx = types.ModuleType("conda_sboms.cyclonedx")
        cyclonedx.export_cyclonedx_json = lambda environment: environment  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "conda_sboms", package)
        monkeypatch.setitem(sys.modules, "conda_sboms.cyclonedx", cyclonedx)
        monkeypatch.delitem(sys.modules, "conda_sboms.settings", raising=False)

    def unexpected_export(*args: object, **kwargs: object) -> int:
        raise AssertionError("export must not run without the metadata API")

    monkeypatch.setattr(sbom_module, "execute_export", unexpected_export)

    with pytest.raises(CondaValueError, match=r"conda-sboms >=0\.2\.0"):
        execute_sbom(sbom_args(product_name="Acme Runtime", product_version="2026.08"))


@pytest.mark.parametrize("api", ["missing", "old"], ids=["missing", "old"])
def test_sbom_reproducible_requires_current_conda_sboms_api(
    monkeypatch: pytest.MonkeyPatch,
    api: str,
) -> None:
    if api == "missing":
        monkeypatch.setitem(sys.modules, "conda_sboms", None)
        monkeypatch.delitem(sys.modules, "conda_sboms.cyclonedx", raising=False)
    else:

        class OldExporter:
            def __init__(
                self,
                environment: object,
                *,
                metadata: object | None = None,
            ) -> None:
                del environment, metadata

        package = types.ModuleType("conda_sboms")
        package.__path__ = []  # type: ignore[attr-defined]
        cyclonedx = types.ModuleType("conda_sboms.cyclonedx")
        cyclonedx.CycloneDXExporter = OldExporter  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "conda_sboms", package)
        monkeypatch.setitem(sys.modules, "conda_sboms.cyclonedx", cyclonedx)

    def unexpected_export(*args: object, **kwargs: object) -> int:
        raise AssertionError("export must not run without the reproducible API")

    monkeypatch.setattr(sbom_module, "execute_export", unexpected_export)

    with pytest.raises(CondaValueError, match=r"conda-sboms >=0\.3\.0"):
        execute_sbom(sbom_args(reproducible=True))


def test_sbom_without_metadata_uses_generic_exporter_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[Callable | None] = []
    monkeypatch.setitem(sys.modules, "conda_sboms", None)
    monkeypatch.delitem(sys.modules, "conda_sboms.cyclonedx", raising=False)
    monkeypatch.delitem(sys.modules, "conda_sboms.settings", raising=False)

    def execute_export(
        args: argparse.Namespace,
        *,
        console: Console | None = None,
        export_environment: Callable | None = None,
        include_requested_packages: bool = False,
        host_prefix_only: bool = False,
    ) -> int:
        del args, console
        assert include_requested_packages is True
        assert host_prefix_only is True
        callbacks.append(export_environment)
        return 23

    monkeypatch.setattr(sbom_module, "execute_export", execute_export)

    assert execute_sbom(sbom_args()) == 23
    assert callbacks == [None]


def test_sbom_from_lockfile_generates_valid_reproducible_cyclonedx(
    exact_lockfile_export: tuple[Path, str, str],
    rich_console: Console,
) -> None:
    pytest.importorskip("conda_sboms")
    from cyclonedx.schema import SchemaVersion
    from cyclonedx.validation.json import JsonStrictValidator

    workspace, _, digest = exact_lockfile_export
    outputs = [workspace / "first.cdx.json", workspace / "second.cdx.json"]
    for output in outputs:
        assert (
            execute_sbom(
                sbom_args(
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
    assert JsonStrictValidator(SchemaVersion.V1_7).validate_str(content) is None
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
