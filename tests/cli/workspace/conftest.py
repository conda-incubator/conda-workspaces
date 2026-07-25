"""Fixtures for workspace CLI tests."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

import pytest
from conda.base.context import reset_context

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


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
