"""Tests for conda_workspaces.env_spec.

Covers the ``conda.toml`` env-spec plugin only.  The ``conda.lock``
loader now lives in :mod:`conda_workspaces.lockfile` and is tested in
``tests/test_lockfile.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

import conda_workspaces.env_spec as env_spec_mod
from conda_workspaces.env_spec import (
    ALIASES,
    DEFAULT_FILENAMES,
    FORMAT,
    CondaWorkspaceSpec,
)


class _FakeContext:
    """Lightweight stand-in for conda's context with a settable subdir."""

    def __init__(self, subdir: str = "linux-64") -> None:
        self.subdir = subdir


@pytest.fixture(autouse=True)
def _patch_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``context`` inside env_spec with a fake set to linux-64.

    Autouse ensures this applies to all tests in this module.
    """
    monkeypatch.setattr(env_spec_mod, "context", _FakeContext("linux-64"))


def test_plugin_metadata_is_module_level() -> None:
    """Plugin metadata is exposed as module-level ``Final`` constants."""
    assert FORMAT == "conda-workspaces"
    assert isinstance(ALIASES, tuple)
    assert DEFAULT_FILENAMES == ("conda.toml",)


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        (
            "conda.toml",
            (
                '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
                'platforms = ["linux-64"]\n'
            ),
            True,
        ),
        ("pixi.toml", '[workspace]\nname = "x"\n', False),
        ("conda.toml", '[project]\nname = "x"\n', False),
        ("conda.toml", "not valid toml [[[", False),
        ("conda.toml", None, False),
    ],
    ids=["conda-toml", "pixi-toml", "no-workspace", "invalid-toml", "missing"],
)
def test_conda_workspace_spec_can_handle(
    tmp_path: Path, filename: str, content: str | None, expected: bool
) -> None:
    path = tmp_path / filename
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert CondaWorkspaceSpec(path).can_handle() is expected


def test_conda_workspace_spec_env_returns_environment(tmp_path: Path) -> None:
    """env property parses manifest and returns an Environment."""
    path = tmp_path / "conda.toml"
    path.write_text(
        '[workspace]\nname = "myproj"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n'
        "[dependencies]\n"
        'python = ">=3.10"\n',
        encoding="utf-8",
    )

    spec = CondaWorkspaceSpec(path)
    env = spec.env

    assert env.name == "myproj"
    assert env.platform == "linux-64"
    assert len(env.requested_packages) == 1
    assert env.requested_packages[0].name == "python"
    assert "conda-forge" in env.config.channels


def test_conda_workspace_spec_env_redacts_relative_channel_token(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conda.toml"
    path.write_text(
        '[workspace]\nchannels = ["t/SENSITIVE-VALUE/private"]\n'
        'platforms = ["linux-64"]\n',
        encoding="utf-8",
    )

    env = CondaWorkspaceSpec(path).env

    assert env.config.channels == ("https://conda.anaconda.org/private",)


def test_conda_workspace_spec_env_pypi_deps_as_external(tmp_path: Path) -> None:
    """PyPI dependencies appear as external_packages under 'pip'."""
    path = tmp_path / "conda.toml"
    path.write_text(
        '[workspace]\nname = "proj"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n'
        "[dependencies]\n"
        'python = ">=3.10"\n\n'
        "[pypi-dependencies]\n"
        'requests = "*"\n',
        encoding="utf-8",
    )

    spec = CondaWorkspaceSpec(path)
    env = spec.env

    assert "pip" in env.external_packages
    assert len(env.external_packages["pip"]) == 1


def test_conda_workspace_spec_env_name_fallback_to_dirname(
    tmp_path: Path,
) -> None:
    """When workspace has no name, falls back to parent directory."""
    path = tmp_path / "conda.toml"
    path.write_text(
        '[workspace]\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n'
        "[dependencies]\n"
        'python = ">=3.10"\n',
        encoding="utf-8",
    )

    spec = CondaWorkspaceSpec(path)
    env = spec.env
    assert env.name == tmp_path.name
