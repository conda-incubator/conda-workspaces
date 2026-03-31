"""Serialize ``WorkspaceConfig`` + tasks into a ``conda.toml`` TOML document.

Shared helper used by importers that parse via the existing parser
infrastructure (pixi.toml, pyproject.toml) and then re-serialize
the result as a conda-native manifest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit

from ..models import Feature
from ..parsers.toml import CondaTomlParser

if TYPE_CHECKING:
    from ..models import Task, WorkspaceConfig


def config_to_toml(
    config: WorkspaceConfig,
    tasks: dict[str, Task] | None = None,
) -> tomlkit.TOMLDocument:
    """Convert a parsed workspace config (and optional tasks) to a TOML document."""
    doc = tomlkit.document()

    ws = tomlkit.table()
    if config.name:
        ws.add("name", config.name)
    if config.channels:
        ws.add("channels", [ch.canonical_name for ch in config.channels])
    if config.platforms:
        ws.add("platforms", list(config.platforms))
    if config.channel_priority:
        ws.add("channel-priority", config.channel_priority)
    doc.add("workspace", ws)

    default_feature = config.features.get(Feature.DEFAULT_NAME)
    if default_feature and default_feature.conda_dependencies:
        deps: dict[str, str] = {}
        for name, ms in default_feature.conda_dependencies.items():
            deps[name] = str(ms.version) if ms.version else "*"
        doc.add("dependencies", deps)

    if default_feature and default_feature.pypi_dependencies:
        pypi: dict[str, str] = {}
        for name, dep in default_feature.pypi_dependencies.items():
            pypi[name] = dep.spec if dep.spec else "*"
        doc.add("pypi-dependencies", pypi)

    if default_feature and (
        default_feature.activation_scripts or default_feature.activation_env
    ):
        activation = tomlkit.table()
        if default_feature.activation_scripts:
            activation.add("scripts", list(default_feature.activation_scripts))
        if default_feature.activation_env:
            activation.add("env", dict(default_feature.activation_env))
        doc.add("activation", activation)

    if default_feature and default_feature.system_requirements:
        doc.add("system-requirements", dict(default_feature.system_requirements))

    if default_feature and default_feature.target_conda_dependencies:
        _add_target_deps(doc, default_feature)

    for feat_name, feature in config.features.items():
        if feature.is_default:
            continue
        _add_feature(doc, feature)

    if config.environments:
        envs = tomlkit.table()
        for env_name, env in config.environments.items():
            if env.is_default and not env.features:
                envs.add(env_name, [])
            elif env.no_default_feature:
                envs.add(
                    env_name,
                    {"features": env.features, "no-default-feature": True},
                )
            elif env.features:
                envs.add(env_name, {"features": env.features})
            else:
                envs.add(env_name, [])
        doc.add("environments", envs)

    if tasks:
        parser = CondaTomlParser()
        task_table = tomlkit.table()
        for name, task in tasks.items():
            task_table.add(name, parser.task_to_toml_inline(task))
        doc.add("tasks", task_table)

    return doc


def _add_feature(doc: tomlkit.TOMLDocument, feature: Feature) -> None:
    """Add ``[feature.<name>.*]`` tables to *doc*."""
    if "feature" not in doc:
        doc.add("feature", tomlkit.table(is_super_table=True))
    feat_container = doc["feature"]

    feat_tbl = tomlkit.table(is_super_table=True)

    if feature.conda_dependencies:
        deps: dict[str, str] = {}
        for name, ms in feature.conda_dependencies.items():
            deps[name] = str(ms.version) if ms.version else "*"
        feat_tbl.add("dependencies", deps)

    if feature.pypi_dependencies:
        pypi: dict[str, str] = {}
        for name, dep in feature.pypi_dependencies.items():
            pypi[name] = dep.spec if dep.spec else "*"
        feat_tbl.add("pypi-dependencies", pypi)

    if feature.channels:
        feat_tbl.add("channels", [ch.canonical_name for ch in feature.channels])

    if feature.platforms:
        feat_tbl.add("platforms", list(feature.platforms))

    if feature.system_requirements:
        feat_tbl.add("system-requirements", dict(feature.system_requirements))

    if feature.activation_scripts or feature.activation_env:
        activation = tomlkit.table()
        if feature.activation_scripts:
            activation.add("scripts", list(feature.activation_scripts))
        if feature.activation_env:
            activation.add("env", dict(feature.activation_env))
        feat_tbl.add("activation", activation)

    if feature.target_conda_dependencies:
        target_tbl = tomlkit.table(is_super_table=True)
        for platform, platform_deps in feature.target_conda_dependencies.items():
            plat_tbl = tomlkit.table()
            plat_deps: dict[str, str] = {}
            for name, ms in platform_deps.items():
                plat_deps[name] = str(ms.version) if ms.version else "*"
            plat_tbl.add("dependencies", plat_deps)
            target_tbl.add(platform, plat_tbl)
        feat_tbl.add("target", target_tbl)

    feat_container.add(feature.name, feat_tbl)


def _add_target_deps(doc: tomlkit.TOMLDocument, feature: Feature) -> None:
    """Add ``[target.<platform>.dependencies]`` for the default feature."""
    if "target" not in doc:
        doc.add("target", tomlkit.table(is_super_table=True))
    target = doc["target"]

    for platform, platform_deps in feature.target_conda_dependencies.items():
        plat_tbl = tomlkit.table()
        plat_deps: dict[str, str] = {}
        for name, ms in platform_deps.items():
            plat_deps[name] = str(ms.version) if ms.version else "*"
        plat_tbl.add("dependencies", plat_deps)
        target.add(platform, plat_tbl)
