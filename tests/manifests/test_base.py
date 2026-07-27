"""Tests for shared manifest parser operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from conda_workspaces.exceptions import ManifestExistsError
from conda_workspaces.manifests.base import ManifestParser

if TYPE_CHECKING:
    from pathlib import Path


def test_copy_manifest_refuses_dangling_destination_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest = source / "conda.toml"
    manifest.write_text("[workspace]\nname = 'source'\n", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    target = destination / manifest.name
    outside = tmp_path / "outside.toml"
    target.symlink_to(outside)

    with pytest.raises(ManifestExistsError, match="already exists"):
        ManifestParser.copy_manifest(manifest, destination)

    assert target.is_symlink()
    assert not outside.exists()
