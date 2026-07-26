"""Serialize ``WorkspaceConfig`` + tasks into a ``conda.toml`` TOML document.

Shared helper used by importers that parse via the existing parser
infrastructure (pixi.toml, pyproject.toml) and then re-serialize
the result as a conda-native manifest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import tomlkit

from ..manifests.toml import CondaTomlParser, WorkspaceDependencyResolver
from ..models import Feature

if TYPE_CHECKING:
    from typing import Any

    from conda.models.match_spec import MatchSpec
    from tomlkit.items import Table

    from ..models import Environment, Task, WorkspaceConfig


def config_to_toml(
    config: WorkspaceConfig,
    tasks: dict[str, Task] | None = None,
    *,
    source: dict[str, Any] | None = None,
) -> tomlkit.TOMLDocument:
    """Convert a parsed workspace config (and optional tasks) to a TOML document."""
    doc = tomlkit.document()
    source = source or {}

    ws = tomlkit.table()
    if config.name:
        ws.add("name", config.name)
    if config.channels:
        ws.add("channels", [ch.canonical_name for ch in config.channels])
    if config.platforms:
        ws.add("platforms", config.platforms_for_toml())
    if config.channel_priority:
        ws.add("channel-priority", config.channel_priority)
    if config.workspace_dependencies:
        ws.add(
            "dependencies",
            _conda_dependencies_to_toml(
                config.workspace_dependencies,
                source.get("workspace", {}).get("dependencies", {}),
            ),
        )
    doc.add("workspace", ws)

    default_feature = config.features.get(Feature.DEFAULT_NAME)
    if default_feature and default_feature.conda_dependencies:
        doc.add(
            "dependencies",
            tomlkit.item(
                _conda_dependencies_to_toml(
                    default_feature.conda_dependencies,
                    source.get("dependencies", {}),
                )
            ),
        )

    if default_feature and default_feature.pypi_dependencies:
        doc.add(
            "pypi-dependencies",
            tomlkit.item(
                {
                    name: dependency.to_toml()
                    for name, dependency in default_feature.pypi_dependencies.items()
                }
            ),
        )

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
        doc.add(
            "system-requirements",
            tomlkit.item(dict(default_feature.system_requirements)),
        )

    if default_feature and (
        default_feature.target_conda_dependencies
        or default_feature.target_pypi_dependencies
    ):
        _add_target_overrides(doc, default_feature, source)

    for feat_name, feature in config.features.items():
        if feature.is_default:
            continue
        _add_feature(doc, feature, source.get("feature", {}).get(feat_name, {}))

    if config.environments:
        envs = tomlkit.table()
        for env_name, env in config.environments.items():
            if (
                env.conda_dependencies
                or env.pypi_dependencies
                or env.target_conda_dependencies
                or env.target_pypi_dependencies
            ):
                env_table = tomlkit.table()
                if env.features:
                    env_table.add("features", env.features)
                if env.no_default_feature:
                    env_table.add("no-default-feature", True)
                if env.conda_dependencies:
                    env_table.add(
                        "dependencies",
                        _conda_dependencies_to_toml(
                            env.conda_dependencies,
                            source.get("environments", {})
                            .get(env_name, {})
                            .get("dependencies", {}),
                        ),
                    )
                if env.pypi_dependencies:
                    env_table.add(
                        "pypi-dependencies",
                        {
                            name: dependency.to_toml()
                            for name, dependency in env.pypi_dependencies.items()
                        },
                    )
                if env.target_conda_dependencies or env.target_pypi_dependencies:
                    _add_target_overrides(
                        env_table,
                        env,
                        source.get("environments", {}).get(env_name, {}),
                    )
                envs.add(env_name, env_table)
            elif env.no_default_feature:
                envs.add(
                    env_name,
                    {"features": env.features, "no-default-feature": True},
                )
            elif env.is_default and not env.features:
                envs.add(env_name, [])
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


def _conda_dependencies_to_toml(
    dependencies: dict[str, MatchSpec],
    source: dict[str, Any],
) -> dict[str, object]:
    """Serialize MatchSpecs while retaining workspace membership markers."""
    result: dict[str, object] = {}
    for name, spec in dependencies.items():
        raw = source.get(name)
        if isinstance(raw, dict) and raw.get("workspace") is True:
            result[name] = raw
        else:
            result[name] = WorkspaceDependencyResolver.match_spec_to_toml(spec)
    return result


def _add_feature(
    doc: tomlkit.TOMLDocument,
    feature: Feature,
    source: dict[str, Any],
) -> None:
    """Add ``[feature.<name>.*]`` tables to *doc*."""
    if "feature" not in doc:
        doc.add("feature", tomlkit.table(is_super_table=True))
    feat_container = cast("Table", doc["feature"])

    feat_tbl = tomlkit.table(is_super_table=True)

    if feature.conda_dependencies:
        feat_tbl.add(
            "dependencies",
            _conda_dependencies_to_toml(
                feature.conda_dependencies,
                source.get("dependencies", {}),
            ),
        )

    if feature.pypi_dependencies:
        feat_tbl.add(
            "pypi-dependencies",
            {
                name: dependency.to_toml()
                for name, dependency in feature.pypi_dependencies.items()
            },
        )

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

    if feature.target_conda_dependencies or feature.target_pypi_dependencies:
        _add_target_overrides(feat_tbl, feature, source)

    feat_container.add(feature.name, feat_tbl)


def _add_target_overrides(
    parent: tomlkit.TOMLDocument | Table,
    owner: Feature | Environment,
    source: dict[str, Any],
) -> None:
    """Add lossless target dependency overrides for *owner*."""
    if "target" not in parent:
        parent.add("target", tomlkit.table(is_super_table=True))
    target = cast("Table", parent["target"])

    platforms = sorted(
        set(owner.target_conda_dependencies) | set(owner.target_pypi_dependencies)
    )
    for platform in platforms:
        plat_tbl = tomlkit.table()
        target_source = source.get("target", {}).get(platform, {})
        if conda_dependencies := owner.target_conda_dependencies.get(platform):
            plat_tbl.add(
                "dependencies",
                _conda_dependencies_to_toml(
                    conda_dependencies,
                    target_source.get("dependencies", {}),
                ),
            )
        if pypi_dependencies := owner.target_pypi_dependencies.get(platform):
            plat_tbl.add(
                "pypi-dependencies",
                {
                    name: dependency.to_toml()
                    for name, dependency in pypi_dependencies.items()
                },
            )
        target.add(platform, plat_tbl)
