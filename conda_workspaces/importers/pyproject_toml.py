"""Import ``pyproject.toml`` into a ``conda.toml`` workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..parsers import find_parser
from .base import ManifestImporter
from .serialize import config_to_toml

if TYPE_CHECKING:
    from pathlib import Path
    from typing import ClassVar

    import tomlkit


class PyprojectTomlImporter(ManifestImporter):
    """Convert a ``pyproject.toml`` file to a ``conda.toml`` document."""

    filenames: ClassVar[tuple[str, ...]] = ("pyproject.toml",)

    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        parser = find_parser(path)
        config = parser.parse(path)
        tasks = parser.parse_tasks(path)
        return config_to_toml(config, tasks)


convert = PyprojectTomlImporter().convert
