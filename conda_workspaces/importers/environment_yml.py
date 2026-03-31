"""Import ``environment.yml`` into a ``conda.toml`` workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit
from conda.base.context import context as conda_context

from .base import ManifestImporter

if TYPE_CHECKING:
    from pathlib import Path
    from typing import ClassVar


class EnvironmentYmlImporter(ManifestImporter):
    """Convert an ``environment.yml`` file to a ``conda.toml`` document."""

    filenames: ClassVar[tuple[str, ...]] = ("environment.yml", "environment.yaml")
    label: ClassVar[str] = "environment.yml"

    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        data = self.load_yaml(path)

        doc = tomlkit.document()

        ws = tomlkit.table()
        ws.add("name", data.get("name", path.parent.name))
        ws.add("channels", data.get("channels", ["conda-forge"]))
        ws.add("platforms", data.get("platforms", [conda_context.subdir]))
        doc.add("workspace", ws)

        raw_deps = data.get("dependencies", [])
        conda_deps = self.parse_conda_deps(raw_deps)
        pypi_deps = self.parse_pip_deps(raw_deps)

        if conda_deps:
            doc.add("dependencies", conda_deps)
        if pypi_deps:
            doc.add("pypi-dependencies", pypi_deps)

        return doc


convert = EnvironmentYmlImporter().convert
