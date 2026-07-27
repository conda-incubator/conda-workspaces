"""Import ``pyproject.toml`` into a ``conda.toml`` workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..manifests import find_parser
from .base import ManifestImporter
from .serialize import config_to_toml

if TYPE_CHECKING:
    from pathlib import Path
    from typing import ClassVar

    import tomlkit


class PyprojectTomlImporter(ManifestImporter):
    """Convert a ``pyproject.toml`` file to a ``conda.toml`` document."""

    filenames: ClassVar[tuple[str, ...]] = ("pyproject.toml",)
    label: ClassVar[str] = "pyproject.toml"

    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        parser = find_parser(path)
        content = parser.read_manifest_text(path)
        data = parser.parse_toml_text_with_redacted_errors(content, path).unwrap()
        config = parser.parse_data_with_redacted_errors(data, path)
        tasks = parser.parse_tasks_data(data)
        tool = data.get("tool", {})
        conda = tool.get("conda", {})
        source = conda if conda.get("workspace") else tool.get("pixi", {})
        return config_to_toml(config, tasks, source=source)
