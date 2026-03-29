"""Parser for conda.toml workspace manifests and shared TOML helpers.

The ``CondaTomlParser`` handles ``conda.toml`` — the conda-native
workspace format.  Helper functions for parsing channels, dependencies,
environments, and target overrides are shared with
``pixi_toml.py`` and ``pyproject_toml.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import tomlkit

from ..exceptions import WorkspaceParseError
from ..models import (
    Channel,
    Environment,
    MatchSpec,
    PyPIDependency,
)
from .base import WorkspaceParser

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from ..models import (
        Feature,
        WorkspaceConfig,
    )

log = logging.getLogger(__name__)


class CondaTomlParser(WorkspaceParser):
    """Parse ``conda.toml`` manifests.

    This is the conda-native format that mirrors pixi.toml structure
    but uses ``[workspace]`` exclusively (no ``[project]`` fallback).
    """

    filenames = ("conda.toml",)
    extensions = (".toml",)

    def can_handle(self, path: Path) -> bool:
        return path.name in self.filenames

    def has_workspace(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return "workspace" in data

    def parse(self, path: Path) -> WorkspaceConfig:
        # Re-use pixi parser logic — the format is structurally identical.
        # Import inline to avoid circular dependency (pixi_toml imports toml).
        from .pixi_toml import PixiTomlParser

        pixi_parser = PixiTomlParser()
        try:
            config = pixi_parser.parse(path)
        except WorkspaceParseError:
            raise
        except Exception as exc:
            raise WorkspaceParseError(path, str(exc)) from exc
        config.manifest_path = str(path)
        return config


def _parse_channels(raw: list[Any]) -> list[Channel]:
    """Parse a channels list, handling both strings and dicts."""
    channels: list[Channel] = []
    for item in raw:
        if isinstance(item, str):
            channels.append(Channel(item))
        elif isinstance(item, dict):
            if "priority" in item:
                log.debug(
                    "Channel priority is not supported by conda; "
                    "ignoring priority=%s for channel '%s'",
                    item["priority"],
                    item["channel"],
                )
            channels.append(Channel(item["channel"]))
    return channels


def _parse_conda_deps(raw: dict[str, Any]) -> dict[str, MatchSpec]:
    """Parse conda dependency specs into MatchSpec objects."""
    deps: dict[str, MatchSpec] = {}
    for name, spec in raw.items():
        if isinstance(spec, str):
            deps[name] = MatchSpec(f"{name} {spec}".strip())
        elif isinstance(spec, dict):
            version = spec.get("version", "")
            build = spec.get("build", "")
            parts = [name]
            if version:
                parts.append(version)
            if build:
                parts.append(build)
            deps[name] = MatchSpec(" ".join(parts))
        else:
            deps[name] = MatchSpec(f"{name} {spec}")
    return deps


def _parse_pypi_deps(raw: dict[str, Any]) -> dict[str, PyPIDependency]:
    """Parse PyPI dependency specs."""
    deps: dict[str, PyPIDependency] = {}
    for name, spec in raw.items():
        if isinstance(spec, str):
            deps[name] = PyPIDependency(name=name, spec=spec)
        elif isinstance(spec, dict):
            extras = spec.get("extras", [])
            deps[name] = PyPIDependency(
                name=name,
                spec=spec.get("version", ""),
                extras=tuple(extras) if extras else (),
                path=spec.get("path"),
                editable=spec.get("editable", False),
                git=spec.get("git"),
                url=spec.get("url"),
            )
        else:
            deps[name] = PyPIDependency(name=name, spec=str(spec))
    return deps


def _parse_environment(name: str, raw: Any, path: Path) -> Environment:
    """Parse a single environment entry.

    Environments can be specified as:
    - A list of feature names: ``env = ["feat1", "feat2"]``
    - A dict with keys: ``env = {features = [...], solve-group = "..."}``
    """
    if isinstance(raw, list):
        return Environment(name=name, features=raw)
    if isinstance(raw, dict):
        return Environment(
            name=name,
            features=list(raw.get("features", [])),
            solve_group=raw.get("solve-group"),
            no_default_feature=raw.get("no-default-feature", False),
        )
    raise WorkspaceParseError(
        path,
        f"Invalid environment definition for '{name}': "
        f"expected list or dict, got {type(raw).__name__}",
    )


def _parse_target_overrides(target_data: dict[str, Any], feature: Feature) -> None:
    """Parse ``[target.<platform>]`` dep overrides into a feature."""
    for platform, tdata in target_data.items():
        conda = _parse_conda_deps(tdata.get("dependencies", {}))
        if conda:
            feature.target_conda_dependencies[platform] = conda

        pypi = _parse_pypi_deps(tdata.get("pypi-dependencies", {}))
        if pypi:
            feature.target_pypi_dependencies[platform] = pypi
