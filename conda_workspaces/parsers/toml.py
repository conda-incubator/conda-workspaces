"""Parser for conda.toml manifests and shared TOML helpers.

The ``CondaTomlParser`` handles ``conda.toml`` — the conda-native
manifest format for both workspace configuration and task definitions.

Helper functions for parsing channels, dependencies, environments,
and target overrides are shared with ``pixi_toml.py`` and
``pyproject_toml.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import tomlkit

from ..exceptions import TaskNotFoundError, TaskParseError, WorkspaceParseError
from ..models import (
    Channel,
    Environment,
    Feature,
    MatchSpec,
    PyPIDependency,
)
from .base import ManifestParser
from .normalize import parse_tasks_and_targets

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from tomlkit.items import InlineTable

    from ..models import Task, WorkspaceConfig

log = logging.getLogger(__name__)


class CondaTomlParser(ManifestParser):
    """Parse ``conda.toml`` manifests (workspace and tasks).

    This is the conda-native format that mirrors pixi.toml structure
    but uses ``[workspace]`` exclusively (no ``[project]`` fallback).
    """

    filenames = ("conda.toml",)

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

    def has_tasks(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return bool(data.get("tasks"))

    def parse_tasks(self, path: Path) -> dict[str, Task]:
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8")).unwrap()
        except Exception as exc:
            raise TaskParseError(str(path), str(exc)) from exc
        return parse_tasks_and_targets(data)

    def add_task(self, path: Path, name: str, task: Task) -> None:
        if path.exists():
            doc = tomlkit.loads(path.read_text(encoding="utf-8"))
        else:
            doc = tomlkit.document()

        tasks_section = doc.setdefault("tasks", tomlkit.table())
        tasks_section[name] = self.task_to_toml_inline(task)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    def remove_task(self, path: Path, name: str) -> None:
        doc = tomlkit.loads(path.read_text(encoding="utf-8"))
        tasks_section = doc.get("tasks", {})
        if name not in tasks_section:
            raise TaskNotFoundError(name, list(tasks_section.keys()))
        del tasks_section[name]
        self.remove_target_overrides(doc, name)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def tasks_to_toml(tasks: dict[str, Task]) -> str:
    """Serialize a full task dict to ``conda.toml`` TOML string."""
    parser = CondaTomlParser()
    doc = tomlkit.document()

    task_table = tomlkit.table()
    for name, task in tasks.items():
        task_table.add(name, parser.task_to_toml_inline(task))
    doc.add("tasks", task_table)

    targets: dict[str, dict[str, str | InlineTable]] = {}
    for name, task in tasks.items():
        if not task.platforms:
            continue
        for platform, override in task.platforms.items():
            override_table = tomlkit.inline_table()
            if override.cmd is not None:
                override_table.append("cmd", override.cmd)
            if override.env is not None:
                override_table.append("env", dict(override.env))
            if override.cwd is not None:
                override_table.append("cwd", override.cwd)
            if override.clean_env is not None:
                override_table.append("clean-env", override.clean_env)
            if override.inputs is not None:
                override_table.append("inputs", list(override.inputs))
            if override.outputs is not None:
                override_table.append("outputs", list(override.outputs))
            if override.args is not None:
                override_table.append("args", [a.to_toml() for a in override.args])
            if override.depends_on is not None:
                override_table.append(
                    "depends-on",
                    [d.to_toml() for d in override.depends_on],
                )
            if len(override_table) == 1 and "cmd" in override_table:
                targets.setdefault(platform, {})[name] = str(override_table["cmd"])
            else:
                targets.setdefault(platform, {})[name] = override_table

    for platform, platform_tasks in targets.items():
        target_tbl = tomlkit.table(is_super_table=True)
        tasks_tbl = tomlkit.table()
        for tname, tval in platform_tasks.items():
            tasks_tbl.add(tname, tval)
        target_tbl.add("tasks", tasks_tbl)
        doc.setdefault("target", tomlkit.table(is_super_table=True)).add(
            platform, target_tbl
        )

    return tomlkit.dumps(doc)


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
    - A dict with keys: ``env = {features = [...]}``
    """
    if isinstance(raw, list):
        return Environment(name=name, features=raw)
    if isinstance(raw, dict):
        return Environment(
            name=name,
            features=list(raw.get("features", [])),
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


def _parse_feature(name: str, feat_data: dict[str, Any]) -> Feature:
    """Parse a single ``[feature.<name>]`` table into a Feature.

    Shared by ``PixiTomlParser`` and ``PyprojectTomlParser`` — the
    per-feature logic is identical once the data dict is resolved.
    """
    feature = Feature(name=name)
    feature.conda_dependencies = _parse_conda_deps(feat_data.get("dependencies", {}))
    feature.pypi_dependencies = _parse_pypi_deps(feat_data.get("pypi-dependencies", {}))
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
    return feature


def _parse_features_and_envs(
    source: dict[str, Any],
    config: WorkspaceConfig,
    path: Path,
) -> None:
    """Parse features and environments from *source* into *config*.

    Adds the default feature (from top-level deps/activation/system-reqs),
    all named features, and all environments.  Shared by
    ``PixiTomlParser`` and ``PyprojectTomlParser``.
    """
    config.features[Feature.DEFAULT_NAME] = _parse_feature(Feature.DEFAULT_NAME, source)

    for feat_name, feat_data in source.get("feature", {}).items():
        config.features[feat_name] = _parse_feature(feat_name, feat_data)

    envs_data = source.get("environments", {})
    if envs_data:
        for env_name, env_val in envs_data.items():
            config.environments[env_name] = _parse_environment(env_name, env_val, path)
    else:
        config.environments[Environment.DEFAULT_NAME] = Environment(
            name=Environment.DEFAULT_NAME
        )
