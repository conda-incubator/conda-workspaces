"""Parser for pixi.toml workspace manifests.

Reads ``[workspace]`` (or ``[project]`` for legacy manifests),
``[dependencies]``, ``[pypi-dependencies]``, ``[feature.*]``,
``[environments]``, and ``[target.*]`` tables from pixi.toml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit

from ..exceptions import WorkspaceParseError
from ..models import (
    Environment,
    Feature,
    WorkspaceConfig,
)
from .base import WorkspaceParser
from .toml import (
    _parse_channels,
    _parse_conda_deps,
    _parse_environment,
    _parse_pypi_deps,
    _parse_target_overrides,
)

if TYPE_CHECKING:
    from pathlib import Path


class PixiTomlParser(WorkspaceParser):
    """Parse ``pixi.toml`` workspace manifests."""

    filenames = ("pixi.toml",)
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
        return "workspace" in data or "project" in data

    def parse(self, path: Path) -> WorkspaceConfig:
        try:
            text = path.read_text(encoding="utf-8")
            data = tomlkit.loads(text)
        except Exception as exc:
            raise WorkspaceParseError(path, str(exc)) from exc

        root = str(path.parent)

        # workspace table (pixi v0.23+) or legacy project table
        ws = data.get("workspace", data.get("project", {}))
        if not ws:
            raise WorkspaceParseError(path, "No [workspace] or [project] table found")

        config = WorkspaceConfig(
            name=ws.get("name"),
            version=ws.get("version"),
            description=ws.get("description"),
            channels=_parse_channels(ws.get("channels", [])),
            platforms=list(ws.get("platforms", [])),
            root=root,
            manifest_path=str(path),
            channel_priority=ws.get("channel-priority"),
        )

        default_feature = Feature(name=Feature.DEFAULT_NAME)
        default_feature.conda_dependencies = _parse_conda_deps(
            data.get("dependencies", {})
        )
        default_feature.pypi_dependencies = _parse_pypi_deps(
            data.get("pypi-dependencies", {})
        )

        # Top-level activation
        activation = data.get("activation", {})
        if activation:
            default_feature.activation_scripts = list(activation.get("scripts", []))
            default_feature.activation_env = dict(activation.get("env", {}))

        # Top-level system-requirements
        sysreq = data.get("system-requirements", {})
        if sysreq:
            default_feature.system_requirements = {k: str(v) for k, v in sysreq.items()}

        # Top-level target overrides
        _parse_target_overrides(data.get("target", {}), default_feature)

        config.features[Feature.DEFAULT_NAME] = default_feature

        for feat_name, feat_data in data.get("feature", {}).items():
            feature = Feature(name=feat_name)
            feature.conda_dependencies = _parse_conda_deps(
                feat_data.get("dependencies", {})
            )
            feature.pypi_dependencies = _parse_pypi_deps(
                feat_data.get("pypi-dependencies", {})
            )
            feature.channels = _parse_channels(feat_data.get("channels", []))
            feature.platforms = list(feat_data.get("platforms", []))

            sysreq = feat_data.get("system-requirements", {})
            if sysreq:
                feature.system_requirements = {k: str(v) for k, v in sysreq.items()}

            activation = feat_data.get("activation", {})
            if activation:
                feature.activation_scripts = list(activation.get("scripts", []))
                feature.activation_env = dict(activation.get("env", {}))

            _parse_target_overrides(feat_data.get("target", {}), feature)

            config.features[feat_name] = feature

        envs_data = data.get("environments", {})
        if envs_data:
            for env_name, env_val in envs_data.items():
                env = _parse_environment(env_name, env_val, path)
                config.environments[env_name] = env
        else:
            config.environments[Environment.DEFAULT_NAME] = Environment(
                name=Environment.DEFAULT_NAME
            )

        return config
