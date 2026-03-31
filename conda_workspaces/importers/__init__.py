"""Manifest importers for ``conda workspace import``.

Each importer converts a foreign manifest format into a
``tomlkit.Document`` representing a ``conda.toml`` workspace manifest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .anaconda_project import AnacondaProjectImporter
from .conda_project import CondaProjectImporter
from .environment_yml import EnvironmentYmlImporter
from .pixi_toml import PixiTomlImporter
from .pyproject_toml import PyprojectTomlImporter

if TYPE_CHECKING:
    from pathlib import Path

    from .base import ManifestImporter

_IMPORTERS: list[ManifestImporter] = [
    EnvironmentYmlImporter(),
    AnacondaProjectImporter(),
    CondaProjectImporter(),
    PixiTomlImporter(),
    PyprojectTomlImporter(),
]


def find_importer(path: Path) -> ManifestImporter:
    """Return the importer that can handle *path*.

    Raises ``ValueError`` if no importer matches.
    """
    for importer in _IMPORTERS:
        if importer.can_handle(path):
            return importer
    supported = ", ".join(
        fn for imp in _IMPORTERS for fn in imp.filenames
    )
    raise ValueError(
        f"Unrecognised manifest format: '{path.name}'. "
        f"Supported filenames: {supported}"
    )
