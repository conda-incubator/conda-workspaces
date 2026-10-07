"""Fixtures for workspace CLI tests."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conda.base.context import reset_context
from conda.models.dist import Dist
from conda.models.records import PackageRecord

from conda_workspaces.cli.main import generate_workspace_parser
from conda_workspaces.cli.workspace import ship

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from conda_workspaces.models import WorkspaceConfig


@pytest.fixture
def ship_args() -> Callable[..., argparse.Namespace]:
    """Parse the common locked workspace selection for ship failure tests."""

    def parse(config: WorkspaceConfig, *options: str) -> argparse.Namespace:
        return generate_workspace_parser().parse_args(
            [
                "--file",
                config.manifest_path,
                "ship",
                "-e",
                "default",
                "--platform",
                "linux-64",
                "-o",
                str(Path(config.root) / "dist"),
                *options,
            ]
        )

    return parse


@pytest.fixture
def record_ship_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., list[list[str]]]:
    """Record the external builder and simulate its output and failures."""

    def record(
        *,
        available: bool = True,
        help_text: str = "--manifest --source-lock --source-environment",
        exit_code: int = 0,
        execution_error: OSError | None = None,
    ) -> list[list[str]]:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            ship,
            "find_spec",
            lambda name: ModuleSpec(name, None) if available else None,
        )

        def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(args))
            assert args[:4] == [sys.executable, "-m", "conda_ship.cli", "build"]
            assert not kwargs.get("shell")
            if execution_error is not None:
                raise execution_error
            if args[-1] == "--help":
                return subprocess.CompletedProcess(args, 0, stdout=help_text, stderr="")
            assert kwargs["stdout"] is sys.stderr
            assert kwargs["stderr"] is sys.stderr
            print("conda-ship build output", file=sys.stderr)
            if exit_code:
                print("conda-ship: unsupported dependency", file=sys.stderr)
            return subprocess.CompletedProcess(args, exit_code)

        monkeypatch.setattr(ship.subprocess, "run", run)
        return calls

    return record


@pytest.fixture
def exact_lockfile_export(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, str, list[dict[str, object]]]:
    """Create a lockfile whose exact package record needs no archive fetch."""
    monkeypatch.chdir(pixi_workspace)
    url = "https://conda.anaconda.org/conda-forge/linux-64/python-3.12.0-h123_0.conda"
    digest = "a" * 64
    pytest_url = (
        "https://conda.anaconda.org/conda-forge/noarch/pytest-8.3.0-pyhd8ed1ab_0.conda"
    )
    pytest_digest = "b" * 64
    (pixi_workspace / "conda.lock").write_text(
        "version: 1\n"
        "environments:\n"
        "  default:\n"
        "    channels:\n"
        "    - url: https://conda.anaconda.org/conda-forge\n"
        "    packages:\n"
        "      linux-64:\n"
        f"      - conda: {url}\n"
        "  test:\n"
        "    channels:\n"
        "    - url: https://conda.anaconda.org/conda-forge\n"
        "    packages:\n"
        "      linux-64:\n"
        f"      - conda: {url}\n"
        f"      - conda: {pytest_url}\n"
        "packages:\n"
        f"- conda: {url}\n"
        f"  sha256: {digest}\n"
        "  license: BSD-3-Clause\n"
        f"- conda: {pytest_url}\n"
        f"  sha256: {pytest_digest}\n"
        "  license: MIT\n",
        encoding="utf-8",
    )

    conversion_calls: list[dict[str, object]] = []

    def records_from_conda_urls(
        metadata_by_url: dict[str, object],
        **kwargs: object,
    ) -> tuple[PackageRecord, ...]:
        del kwargs
        conversion_calls.append(dict(metadata_by_url))
        records = []
        for package_url, metadata in metadata_by_url.items():
            dist = Dist(package_url)
            records.append(
                PackageRecord.from_objects(
                    metadata,
                    name=dist.name,
                    version=dist.version,
                    build=dist.build_string,
                    build_number=dist.build_number,
                    channel=dist.channel,
                    subdir=dist.subdir,
                    fn=dist.to_filename(),
                    url=package_url,
                )
            )
        return tuple(records)

    monkeypatch.setattr(
        "conda_lockfiles.rattler_lock.v6.records_from_conda_urls",
        records_from_conda_urls,
    )
    return pixi_workspace, url, digest, conversion_calls


@pytest.fixture
def rich_platform_lockfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[dict[str, str], tuple[str, ...]], Path]:
    """Create a workspace and lockfile with named Linux platform variants."""

    def create(
        declared_platforms: dict[str, str],
        locked_platforms: tuple[str, ...],
    ) -> Path:
        platform_rows = "\n".join(
            f'  {{ name = "{name}", platform = "linux-64", cuda = "{cuda}" }},'
            for name, cuda in declared_platforms.items()
        )
        package_rows = "\n".join(
            f"      {platform}: []" for platform in locked_platforms
        )
        (tmp_path / "pixi.toml").write_text(
            f"""\
[workspace]
name = "rich-platform-export"
channels = []
platforms = [
{platform_rows}
]
""",
            encoding="utf-8",
        )
        (tmp_path / "conda.lock").write_text(
            "version: 1\n"
            "environments:\n"
            "  default:\n"
            "    channels: []\n"
            "    packages:\n"
            f"{package_rows}\n"
            "packages: []\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        return tmp_path

    return create


@pytest.fixture
def broadened_platform_workspace(tmp_path: Path) -> Path:
    """Create a workspace with a feature-only platform."""
    (tmp_path / "pixi.toml").write_text(
        """\
[workspace]
name = "broadened"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"

[feature.windows]
platforms = ["win-64"]

[environments]
windows = ["windows"]
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def configure_conda_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., None]]:
    """Configure isolated conda channels and matching CLI arguments."""
    condarc = tmp_path / "condarc"

    def configure(
        channels: list[str],
        *,
        channel: list[str] | None = None,
        override_channels: bool = False,
    ) -> None:
        condarc.write_text(f"channels: {json.dumps(channels)}\n", encoding="utf-8")
        reset_context(
            search_path=(condarc,),
            argparse_args=argparse.Namespace(
                channel=channel,
                override_channels=override_channels,
            ),
        )

    with monkeypatch.context() as environment:
        environment.delenv("CONDA_CHANNELS", raising=False)
        configure(["conda-forge"])
        yield configure

    reset_context()
