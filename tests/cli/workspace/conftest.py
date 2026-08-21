"""Fixtures for workspace CLI tests."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

import pytest
from conda.base.context import reset_context
from conda.models.records import PackageRecord

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def exact_lockfile_export(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, str, list[dict[str, object]]]:
    """Create a lockfile whose exact package record needs no archive fetch."""
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
        f"  sha256: {digest}\n"
        "  license: BSD-3-Clause\n",
        encoding="utf-8",
    )

    conversion_calls: list[dict[str, object]] = []

    def records_from_conda_urls(
        metadata_by_url: dict[str, object],
        **kwargs: object,
    ) -> tuple[PackageRecord, ...]:
        del kwargs
        conversion_calls.append(dict(metadata_by_url))
        return tuple(
            PackageRecord(
                name="python",
                version="3.12.0",
                build="h123_0",
                build_number=0,
                channel="https://conda.anaconda.org/conda-forge",
                subdir="linux-64",
                fn="python-3.12.0-h123_0.conda",
                url=package_url,
                sha256=digest,
                license="BSD-3-Clause",
            )
            for package_url in metadata_by_url
        )

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
