"""Import ``pixi.toml`` into a ``conda.toml`` workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..manifests import find_parser
from .base import ManifestImporter
from .serialize import config_to_toml

if TYPE_CHECKING:
    from pathlib import Path
    from typing import ClassVar

    import tomlkit


class PixiTomlImporter(ManifestImporter):
    """Convert a ``pixi.toml`` file to a ``conda.toml`` document."""

    filenames: ClassVar[tuple[str, ...]] = ("pixi.toml",)
    label: ClassVar[str] = "pixi"

    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        parser = find_parser(path)
        content = parser.read_manifest_text(path)
        source = parser.parse_toml_text_with_redacted_errors(content, path).unwrap()
        config = parser.parse_data_with_redacted_errors(source, path)
        tasks = parser.parse_tasks_data(source)
        return config_to_toml(config, tasks, source=source)
