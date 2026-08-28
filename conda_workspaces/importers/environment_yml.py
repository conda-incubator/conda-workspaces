"""Import ``environment.yml`` into a ``conda.toml`` workspace manifest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import tomlkit
from conda.base.context import context as conda_context

from ..exceptions import ManifestImportError
from ..models import redact_url_text
from .base import ManifestImporter

if TYPE_CHECKING:
    from pathlib import Path
    from typing import ClassVar


@dataclass(frozen=True)
class EnvironmentYmlData:
    """Strictly parsed data for one named workspace environment."""

    name: str | None
    prefix: str | None
    channels: tuple[str, ...] | None
    platforms: tuple[str, ...] | None
    conda_dependencies: dict[str, object]
    pypi_dependencies: dict[str, object]


class EnvironmentYmlImporter(ManifestImporter):
    """Convert an ``environment.yml`` file to a ``conda.toml`` document."""

    filenames: ClassVar[tuple[str, ...]] = ("environment.yml", "environment.yaml")
    label: ClassVar[str] = "environment.yml"

    def parse_named_environment(self, path: Path) -> EnvironmentYmlData:
        """Read *path* as strict data for a named workspace environment."""
        try:
            data = self.load_yaml(path)
            allowed_keys = {
                "name",
                "prefix",
                "channels",
                "platforms",
                "dependencies",
                "variables",
            }
            unknown_keys = sorted(
                redact_url_text(str(key)) for key in data if key not in allowed_keys
            )
            if unknown_keys:
                raise ValueError(
                    "Unsupported environment.yml fields: " + ", ".join(unknown_keys)
                )
            if "variables" in data:
                raise ValueError(
                    "environment.yml variables cannot be imported into a named "
                    "workspace environment."
                )

            name: str | None = None
            if "name" in data:
                if not isinstance(data["name"], str):
                    raise ValueError("Manifest name must be a string.")
                name = data["name"]

            prefix: str | None = None
            if "prefix" in data:
                if not isinstance(data["prefix"], str):
                    raise ValueError("Manifest prefix must be a string.")
                prefix = data["prefix"]

            channels: tuple[str, ...] | None = None
            if "channels" in data:
                raw_channels = data["channels"]
                if not isinstance(raw_channels, list) or not all(
                    isinstance(channel, str) for channel in raw_channels
                ):
                    raise ValueError("Manifest channels must be a list of strings.")
                channels = tuple(self.redact_channels(raw_channels))

            platforms: tuple[str, ...] | None = None
            if "platforms" in data:
                raw_platforms = data["platforms"]
                if not isinstance(raw_platforms, list) or not all(
                    isinstance(platform, str) for platform in raw_platforms
                ):
                    raise ValueError("Manifest platforms must be a list of strings.")
                platforms = tuple(raw_platforms)

            raw_dependencies = data.get("dependencies", [])
            return EnvironmentYmlData(
                name=name,
                prefix=prefix,
                channels=channels,
                platforms=platforms,
                conda_dependencies=self.parse_conda_deps(raw_dependencies),
                pypi_dependencies=self.parse_pip_deps(raw_dependencies),
            )
        except ManifestImportError:
            raise
        except Exception as exc:
            raise ManifestImportError(path, redact_url_text(str(exc))) from exc

    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        data = self.load_yaml(path)

        doc = tomlkit.document()

        ws = tomlkit.table()
        ws.add("name", data.get("name", path.parent.name))
        ws.add(
            "channels",
            self.redact_channels(data.get("channels", ["conda-forge"])),
        )
        ws.add("platforms", data.get("platforms", [conda_context.subdir]))
        doc.add("workspace", ws)

        raw_deps = data.get("dependencies", [])
        conda_deps = self.parse_conda_deps(raw_deps)
        pypi_deps = self.parse_pip_deps(raw_deps)

        if conda_deps:
            doc.add("dependencies", tomlkit.item(conda_deps))
        if pypi_deps:
            doc.add("pypi-dependencies", tomlkit.item(pypi_deps))

        return doc
