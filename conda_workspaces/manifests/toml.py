"""Parser for conda.toml manifests and shared TOML helpers.

The ``CondaTomlParser`` handles ``conda.toml`` — the conda-native
manifest format for both workspace configuration and task definitions.

Public helpers for parsing shared TOML workspace tables are reused by
``pixi_toml.py`` and ``pyproject_toml.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import tomlkit

from ..exceptions import TaskNotFoundError, TaskParseError, WorkspaceParseError
from ..models import (
    ArchiveConfig,
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
    from typing import Any, ClassVar, NoReturn

    from tomlkit.items import InlineTable

    from ..models import Task, WorkspaceConfig

log = logging.getLogger(__name__)


class CondaTomlParser(ManifestParser):
    """Parse ``conda.toml`` manifests (workspace and tasks).

    This is the conda-native format that mirrors pixi.toml structure
    but uses ``[workspace]`` exclusively (no ``[project]`` fallback).
    """

    format_alias = "conda"
    filenames = ("conda.toml",)
    exporter_format = "conda-toml"

    def can_handle(self, path: Path) -> bool:
        return path.name in self.filenames

    def has_workspace(self, path: Path) -> bool:
        return "workspace" in self.read_toml(str(path))

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
        return bool(self.read_toml(str(path)).get("tasks"))

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


def parse_archive_config(ws: dict[str, Any]) -> ArchiveConfig:
    """Parse ``[workspace.archive]`` into an ArchiveConfig."""
    archive_data = ws.get("archive", {})
    return ArchiveConfig(
        include=tuple(archive_data.get("include", [])),
        exclude=tuple(archive_data.get("exclude", [])),
        compression=archive_data.get("compression", "zst"),
        compression_level=archive_data.get("compression-level"),
    )


def parse_channels(raw: list[Any]) -> list[Channel]:
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


class WorkspaceDependencyResolver:
    """Resolve conda dependency tables with workspace inheritance.

    ``[workspace.dependencies]`` is a root-level pool. Entries in regular
    dependency tables opt in with ``{ workspace = true }``; after parsing,
    downstream code only sees concrete ``MatchSpec`` objects.
    """

    spec_field_aliases: ClassVar[dict[str, str]] = {
        "version": "version",
        "build": "build",
        "build-number": "build_number",
        "build_number": "build_number",
        "channel": "channel",
        "subdir": "subdir",
        "md5": "md5",
        "sha256": "sha256",
        "url": "url",
        "fn": "fn",
        "file-name": "fn",
        "license": "license",
        "license-family": "license_family",
        "license_family": "license_family",
        "features": "features",
        "track-features": "track_features",
        "track_features": "track_features",
    }
    source_spec_fields: ClassVar[set[str]] = {
        "branch",
        "extras",
        "flags",
        "git",
        "path",
        "rev",
        "subdirectory",
        "tag",
    }

    def __init__(
        self,
        *,
        workspace_dependencies: dict[str, Any] | None = None,
        path: Path | None = None,
    ) -> None:
        self.workspace_dependencies_raw = workspace_dependencies or {}
        self.path = path
        self.workspace_dependencies = self.parse_dependency_table(
            self.workspace_dependencies_raw,
            allow_inheritance=False,
            table_name="[workspace.dependencies]",
        )

    def parse_dependency_table(
        self,
        raw: dict[str, Any],
        *,
        allow_inheritance: bool = True,
        table_name: str = "[dependencies]",
    ) -> dict[str, MatchSpec]:
        """Parse a dependency table into ``MatchSpec`` objects."""
        deps: dict[str, MatchSpec] = {}
        for name, spec in raw.items():
            deps[name] = self.parse_dependency(
                name,
                spec,
                allow_inheritance=allow_inheritance,
                table_name=table_name,
            )
        return deps

    def parse_dependency(
        self,
        name: str,
        spec: Any,
        *,
        allow_inheritance: bool,
        table_name: str,
    ) -> MatchSpec:
        """Parse one dependency entry."""
        if isinstance(spec, str):
            return MatchSpec(f"{name} {spec}".strip())
        if not isinstance(spec, dict):
            return MatchSpec(f"{name} {spec}")

        if "workspace" not in spec:
            fields = self.spec_fields(name, spec, strict_unsupported=False)
            return self.match_spec_from_fields(name, fields)

        if not allow_inheritance:
            self.error(f"{table_name}.{name} cannot use `workspace = true`.")

        workspace = spec["workspace"]
        if workspace is not True:
            self.error(
                f"{table_name}.{name} sets `workspace = {workspace!r}`; "
                "`workspace` can only be true."
            )
        if "version" in spec:
            self.error(
                f"{table_name}.{name} cannot set both `workspace = true` and `version`."
            )
        if name not in self.workspace_dependencies_raw:
            self.error(
                f"{table_name}.{name} inherits from [workspace.dependencies], "
                f"but no workspace dependency named '{name}' exists."
            )

        base_spec = self.workspace_dependencies_raw[name]
        self.reject_source_fields(name, base_spec, "[workspace.dependencies]")
        self.reject_source_fields(name, spec, table_name)

        base_fields = self.spec_fields(name, base_spec, strict_unsupported=True)
        override_fields = self.spec_fields(
            name,
            {k: v for k, v in spec.items() if k != "workspace"},
            strict_unsupported=True,
        )
        fields = {**base_fields, **override_fields}
        return self.match_spec_from_fields(name, fields)

    def spec_fields(
        self,
        name: str,
        spec: Any,
        *,
        strict_unsupported: bool,
    ) -> dict[str, Any]:
        """Return conda ``MatchSpec`` keyword fields for one TOML dependency."""
        if isinstance(spec, str):
            return {"version": spec}
        if not isinstance(spec, dict):
            return {"version": str(spec)}

        unsupported = set(spec) - set(self.spec_field_aliases) - {"workspace"}
        if strict_unsupported and unsupported:
            fields = ", ".join(sorted(unsupported))
            self.error(
                f"Conda dependency '{name}' uses unsupported field(s): {fields}."
            )

        fields: dict[str, Any] = {}
        for raw_key, value in spec.items():
            key = self.spec_field_aliases.get(raw_key)
            if key is None:
                continue
            if value is None or value == "":
                continue
            if isinstance(value, list):
                value = tuple(value)
            fields[key] = value
        return fields

    def reject_source_fields(self, name: str, spec: Any, table_name: str) -> None:
        """Reject pixi source-package fields when inheritance would consume them."""
        if not isinstance(spec, dict):
            return
        unsupported = sorted(set(spec) & self.source_spec_fields)
        if not unsupported:
            return
        fields = ", ".join(unsupported)
        self.error(
            f"{table_name}.{name} uses source dependency field(s) unsupported by "
            f"conda-workspaces inheritance: {fields}."
        )

    def match_spec_from_fields(self, name: str, fields: dict[str, Any]) -> MatchSpec:
        """Construct a ``MatchSpec`` and wrap parse errors with manifest context."""
        try:
            return MatchSpec(name=name, **fields)
        except Exception as exc:
            self.error(f"Invalid conda dependency '{name}': {exc}")

    def error(self, message: str) -> NoReturn:
        """Raise a manifest parse error when a path is available."""
        if self.path is not None:
            raise WorkspaceParseError(self.path, message)
        raise ValueError(message)


def parse_pypi_dependencies(raw: dict[str, Any]) -> dict[str, PyPIDependency]:
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


def parse_environment(name: str, raw: Any, path: Path) -> Environment:
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


def parse_target_overrides(
    target_data: dict[str, Any],
    feature: Feature,
    resolver: WorkspaceDependencyResolver | None = None,
) -> None:
    """Parse ``[target.<platform>]`` dep overrides into a feature."""
    resolver = resolver or WorkspaceDependencyResolver()
    for platform, tdata in target_data.items():
        conda = resolver.parse_dependency_table(
            tdata.get("dependencies", {}),
            table_name=f"[target.{platform}.dependencies]",
        )
        if conda:
            feature.target_conda_dependencies[platform] = conda

        pypi = parse_pypi_dependencies(tdata.get("pypi-dependencies", {}))
        if pypi:
            feature.target_pypi_dependencies[platform] = pypi


def parse_feature(
    name: str,
    feat_data: dict[str, Any],
    resolver: WorkspaceDependencyResolver | None = None,
) -> Feature:
    """Parse a single ``[feature.<name>]`` table into a Feature.

    Shared by ``PixiTomlParser`` and ``PyprojectTomlParser`` — the
    per-feature logic is identical once the data dict is resolved.
    """
    resolver = resolver or WorkspaceDependencyResolver()
    feature = Feature(name=name)
    table_name = (
        "[dependencies]"
        if name == Feature.DEFAULT_NAME
        else f"[feature.{name}.dependencies]"
    )
    feature.conda_dependencies = resolver.parse_dependency_table(
        feat_data.get("dependencies", {}),
        table_name=table_name,
    )
    feature.pypi_dependencies = parse_pypi_dependencies(
        feat_data.get("pypi-dependencies", {})
    )
    feature.channels = parse_channels(feat_data.get("channels", []))
    feature.platforms = list(feat_data.get("platforms", []))

    sysreq = feat_data.get("system-requirements", {})
    if sysreq:
        feature.system_requirements = {k: str(v) for k, v in sysreq.items()}

    activation = feat_data.get("activation", {})
    if activation:
        feature.activation_scripts = list(activation.get("scripts", []))
        feature.activation_env = dict(activation.get("env", {}))

    parse_target_overrides(feat_data.get("target", {}), feature, resolver)
    return feature


def parse_features_and_envs(
    source: dict[str, Any],
    config: WorkspaceConfig,
    path: Path,
) -> None:
    """Parse features and environments from *source* into *config*.

    Adds the default feature (from top-level deps/activation/system-reqs),
    all named features, and all environments.  Shared by
    ``PixiTomlParser`` and ``PyprojectTomlParser``.
    """
    resolver = WorkspaceDependencyResolver(
        workspace_dependencies=source.get("workspace", {}).get("dependencies", {}),
        path=path,
    )
    config.workspace_dependencies = resolver.workspace_dependencies
    config.features[Feature.DEFAULT_NAME] = parse_feature(
        Feature.DEFAULT_NAME,
        source,
        resolver,
    )

    for feat_name, feat_data in source.get("feature", {}).items():
        config.features[feat_name] = parse_feature(feat_name, feat_data, resolver)

    envs_data = source.get("environments", {})
    if envs_data:
        for env_name, env_val in envs_data.items():
            config.environments[env_name] = parse_environment(env_name, env_val, path)
    else:
        config.environments[Environment.DEFAULT_NAME] = Environment(
            name=Environment.DEFAULT_NAME
        )
