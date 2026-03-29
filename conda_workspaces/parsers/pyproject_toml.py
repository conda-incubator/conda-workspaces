"""Parser for pyproject.toml workspace manifests.

Reads workspace configuration from ``pyproject.toml``, trying these
tables in order:

1. ``[tool.conda.workspace]``          – preferred conda-native table
2. ``[tool.conda-workspaces.workspace]`` – legacy conda-native table
3. ``[tool.pixi.workspace]``            – pixi compatibility
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
    from typing import Any


class PyprojectTomlParser(WorkspaceParser):
    """Parse workspace config from ``pyproject.toml``.

    Tries these tables in order:

    1. ``[tool.conda.workspace]`` – preferred conda-native table
    2. ``[tool.conda-workspaces.workspace]`` – legacy table
    3. ``[tool.pixi.workspace]`` – pixi compatibility
    """

    filenames = ("pyproject.toml",)
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
        tool = data.get("tool", {})
        conda = tool.get("conda", {})
        cw = tool.get("conda-workspaces", {})
        pixi = tool.get("pixi", {})
        return bool(
            conda.get("workspace") or cw.get("workspace") or pixi.get("workspace")
        )

    def parse(self, path: Path) -> WorkspaceConfig:
        try:
            text = path.read_text(encoding="utf-8")
            data = tomlkit.loads(text)
        except Exception as exc:
            raise WorkspaceParseError(path, str(exc)) from exc

        tool = data.get("tool", {})
        root = str(path.parent)

        # Try tables in priority order: conda > conda-workspaces > pixi
        conda = tool.get("conda", {})
        cw = tool.get("conda-workspaces", {})
        pixi = tool.get("pixi", {})

        source: dict[str, Any]
        if conda.get("workspace"):
            source = conda
        elif cw.get("workspace"):
            source = cw
        elif pixi.get("workspace"):
            source = pixi
        else:
            raise WorkspaceParseError(
                path,
                "No [tool.conda.workspace], [tool.conda-workspaces.workspace], "
                "or [tool.pixi.workspace] table found",
            )

        ws = source.get("workspace", {})

        config = WorkspaceConfig(
            name=ws.get("name") or data.get("project", {}).get("name"),
            version=ws.get("version") or data.get("project", {}).get("version"),
            description=data.get("project", {}).get("description"),
            channels=_parse_channels(ws.get("channels", [])),
            platforms=list(ws.get("platforms", [])),
            root=root,
            manifest_path=str(path),
            channel_priority=ws.get("channel-priority"),
        )

        default_feature = Feature(name=Feature.DEFAULT_NAME)
        default_feature.conda_dependencies = _parse_conda_deps(
            source.get("dependencies", {})
        )
        default_feature.pypi_dependencies = _parse_pypi_deps(
            source.get("pypi-dependencies", {})
        )

        activation = source.get("activation", {})
        if activation:
            default_feature.activation_scripts = list(activation.get("scripts", []))
            default_feature.activation_env = dict(activation.get("env", {}))

        sysreq = source.get("system-requirements", {})
        if sysreq:
            default_feature.system_requirements = {k: str(v) for k, v in sysreq.items()}

        _parse_target_overrides(source.get("target", {}), default_feature)
        config.features[Feature.DEFAULT_NAME] = default_feature

        for feat_name, feat_data in source.get("feature", {}).items():
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

        envs_data = source.get("environments", {})
        if envs_data:
            for env_name, env_val in envs_data.items():
                env = _parse_environment(env_name, env_val, path)
                config.environments[env_name] = env
        else:
            config.environments[Environment.DEFAULT_NAME] = Environment(
                name=Environment.DEFAULT_NAME
            )

        return config
