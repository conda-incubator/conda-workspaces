"""Data models for workspace configuration.

These dataclasses represent the parsed workspace manifest in a
format-agnostic way.  Parsers convert from pixi.toml / pyproject.toml /
conda.toml into these models; downstream code only works with
these types.

Conda dependencies use :class:`~conda.models.match_spec.MatchSpec`
directly, and channels use :class:`~conda.models.channel.Channel`,
so the workspace layer benefits from conda's own validation, URL
resolution, and spec parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Any

from conda.base.constants import KNOWN_SUBDIRS
from conda.models.channel import Channel  # noqa: TC002
from conda.models.match_spec import MatchSpec  # noqa: TC002

from .exceptions import (
    EnvironmentNotFoundError,
    FeatureNotFoundError,
    PlatformError,
)


@dataclass(frozen=True)
class LockfileStatus:
    """Status of the lockfile relative to the workspace manifest."""

    UP_TO_DATE: ClassVar[str] = "up-to-date"
    OUT_OF_DATE: ClassVar[str] = "out-of-date"
    MISSING: ClassVar[str] = "missing"

    status: str
    reason: str = ""


@dataclass(frozen=True)
class PyPIDependency:
    """A PyPI dependency (PEP 508 string).

    Version-only dependencies are translated to conda equivalents via
    ``conda-pypi``'s grayskull mapping and merged into the same solver
    call as conda deps. Local ``path`` dependencies are built and
    installed post-solve via conda-pypi's build system. Git and URL
    dependencies are parsed for pixi manifest compatibility but are not
    installed yet.
    """

    name: str
    spec: str = ""
    extras: tuple[str, ...] = ()
    path: str | None = None
    editable: bool = False
    git: str | None = None
    url: str | None = None

    def __str__(self) -> str:
        base = self.name
        if self.extras:
            base = f"{base}[{','.join(self.extras)}]"
        if self.git:
            return f"{base} @ git+{self.git}"
        if self.path:
            prefix = "-e " if self.editable else ""
            return f"{prefix}{base} @ {self.path}"
        if self.url:
            return f"{base} @ {self.url}"
        if self.spec:
            return f"{base}{self.spec}"
        return base


@dataclass
class Feature:
    """A composable group of dependencies and settings.

    Features map directly to ``[feature.<name>]`` tables in a pixi manifest.
    They can provide conda dependencies, PyPI dependencies, channel
    overrides, platform restrictions, and environment variables.

    The special feature named ``"default"`` corresponds to the top-level
    workspace dependencies.
    """

    DEFAULT_NAME: ClassVar[str] = "default"

    name: str
    conda_dependencies: dict[str, MatchSpec] = field(default_factory=dict)
    pypi_dependencies: dict[str, PyPIDependency] = field(default_factory=dict)
    channels: list[Channel] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    system_requirements: dict[str, str] = field(default_factory=dict)
    activation_scripts: list[str] = field(default_factory=list)
    activation_env: dict[str, str] = field(default_factory=dict)

    # Per-platform overrides: platform -> deps
    target_conda_dependencies: dict[str, dict[str, MatchSpec]] = field(
        default_factory=dict
    )
    target_pypi_dependencies: dict[str, dict[str, PyPIDependency]] = field(
        default_factory=dict
    )

    @property
    def is_default(self) -> bool:
        return self.name == self.DEFAULT_NAME


@dataclass
class Environment:
    """A named environment composed from one or more features.

    This maps to a ``[environments]`` entry in a pixi manifest.
    An environment inherits the ``default`` feature plus any additional
    features listed in *features*.

    *no_default_feature* can be set to exclude the default feature,
    matching pixi's ``no-default-feature = true`` option.
    """

    DEFAULT_NAME: ClassVar[str] = "default"

    name: str
    features: list[str] = field(default_factory=list)
    no_default_feature: bool = False

    @property
    def is_default(self) -> bool:
        return self.name == self.DEFAULT_NAME


@dataclass(frozen=True)
class ArchiveConfig:
    """Archive settings from ``[workspace.archive]``."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    compression: str = "zst"
    compression_level: int | None = None


@dataclass
class WorkspaceConfig:
    """Complete parsed workspace configuration.

    This is the top-level model that parsers produce.  It contains
    all channels, platforms, features, and environments defined in
    a workspace manifest.

    *manifest_path* points to the file that was parsed (for error
    messages and relative path resolution).
    """

    name: str | None = None
    version: str | None = None
    description: str | None = None

    channels: list[Channel] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    platform_subdirs: dict[str, str] = field(default_factory=dict)
    platform_system_requirements: dict[str, dict[str, str]] = field(
        default_factory=dict
    )

    # Root-level dependency pool from [workspace.dependencies].
    # Concrete feature dependencies may opt in with { workspace = true }.
    workspace_dependencies: dict[str, MatchSpec] = field(default_factory=dict)

    # Features keyed by name; always includes "default"
    features: dict[str, Feature] = field(default_factory=dict)

    # Environments keyed by name; always includes "default"
    environments: dict[str, Environment] = field(default_factory=dict)

    # Workspace root directory (parent of manifest file)
    root: str = ""

    # Path to the manifest file that was parsed
    manifest_path: str = ""

    # Directory for project-local environments (default: .conda/envs)
    envs_dir: str = ".conda/envs"

    # Preview / optional fields
    channel_priority: str | None = None  # "strict" | "flexible" | "disabled"
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)

    platform_requirement_toml_aliases: ClassVar[dict[str, str]] = {
        "glibc": "libc",
        "__glibc": "__glibc",
        "osx": "macos",
        "__osx": "__osx",
        "win": "windows",
        "__win": "__win",
    }
    rich_platform_name_order: ClassVar[tuple[str, ...]] = (
        "cuda",
        "archspec",
        "glibc",
        "linux",
        "osx",
        "win",
    )
    _platform_name_segment_re: ClassVar[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9]+")

    def __post_init__(self) -> None:
        """Ensure the default feature and environment always exist.

        Also validates that all declared platforms map to recognised
        conda subdirs (e.g. ``linux-64``, ``osx-arm64``).  Pixi rich
        platforms may use workspace-scoped names such as
        ``linux-64-cuda``; those names stay in :attr:`platforms`, while
        :attr:`platform_subdirs` records the concrete conda subdir the
        solver should use.
        """
        if Feature.DEFAULT_NAME not in self.features:
            self.features[Feature.DEFAULT_NAME] = Feature(name=Feature.DEFAULT_NAME)
        if Environment.DEFAULT_NAME not in self.environments:
            self.environments[Environment.DEFAULT_NAME] = Environment(
                name=Environment.DEFAULT_NAME
            )

        for platform in self.platforms:
            self.platform_subdirs.setdefault(platform, platform)

        invalid = [
            subdir
            for platform in self.platforms
            if (subdir := self.platform_subdirs[platform]) not in KNOWN_SUBDIRS
        ]
        if invalid:
            raise PlatformError(
                ", ".join(invalid),
                sorted(KNOWN_SUBDIRS),
            )

    @staticmethod
    def default_system_requirements_for_subdir(subdir: str) -> dict[str, str]:
        """Return Pixi's default rich-platform requirements for *subdir*."""
        if subdir.startswith("linux-"):
            return {"glibc": "2.28", "linux": "4.18"}
        if subdir.startswith("osx-"):
            return {"osx": "13.0"}
        return {}

    @classmethod
    def platform_name_segment(cls, value: str) -> str:
        """Sanitize one rich-platform name segment the way Pixi does."""
        return cls._platform_name_segment_re.sub("-", value).strip("-")

    @classmethod
    def synthesize_platform_name(
        cls,
        subdir: str,
        requirements: dict[str, str],
    ) -> str:
        """Return Pixi's generated name for an unnamed rich platform."""
        defaults = cls.default_system_requirements_for_subdir(subdir)
        segments = [subdir]

        canonical_requirements: dict[str, str] = {}
        raw_requirements: dict[str, str] = {}
        for name, value in requirements.items():
            bare_name = name.removeprefix("__")
            if bare_name in cls.rich_platform_name_order:
                canonical_requirements[bare_name] = value
            else:
                raw_requirements[name] = value

        for name in cls.rich_platform_name_order:
            value = canonical_requirements.get(name)
            if value is None or defaults.get(name) == value:
                continue
            key = cls.platform_name_segment(name)
            val = cls.platform_name_segment(value)
            if key and val:
                segments.extend((key, val))

        for name in sorted(raw_requirements):
            value = raw_requirements[name]
            key = cls.platform_name_segment(name.removeprefix("__"))
            val = cls.platform_name_segment(value)
            if key and val:
                segments.extend((key, val))

        return "-".join(segment for segment in segments if segment)

    def platform_subdir(self, platform: str) -> str:
        """Return the concrete conda subdir for a declared platform name."""
        return self.platform_subdirs.get(platform, platform)

    def platform_names_for_subdir(
        self,
        subdir: str,
        platforms: Iterable[str] | None = None,
    ) -> list[str]:
        """Return declared platform names that solve against *subdir*."""
        candidates = list(platforms or self.platforms)
        return [
            platform
            for platform in candidates
            if self.platform_subdir(platform) == subdir
        ]

    def resolve_platform_name(
        self,
        requested: str,
        platforms: Iterable[str] | None = None,
    ) -> str:
        """Resolve a requested name or subdir to a declared platform name."""
        candidates = list(platforms or self.platforms)
        if requested in candidates:
            return requested
        matches = self.platform_names_for_subdir(requested, candidates)
        if matches:
            return matches[0]
        raise PlatformError(requested, sorted(candidates))

    def get_environment(self, name: str) -> Environment:
        """Return the environment with *name*, raising if not found."""
        if name not in self.environments:
            raise EnvironmentNotFoundError(name, list(self.environments.keys()))
        return self.environments[name]

    def resolve_features(self, environment: Environment) -> list[Feature]:
        """Return the ordered list of features for *environment*.

        By default, the ``default`` feature is prepended unless the
        environment sets ``no_default_feature``.
        """
        result: list[Feature] = []
        if not environment.no_default_feature:
            result.append(self.features[Feature.DEFAULT_NAME])

        for fname in environment.features:
            if fname not in self.features:
                raise FeatureNotFoundError(fname, environment.name)
            feat = self.features[fname]
            if feat not in result:
                result.append(feat)

        return result

    def merged_conda_dependencies(
        self,
        environment: Environment,
        platform: str | None = None,
    ) -> dict[str, MatchSpec]:
        """Merge conda dependencies across features for *environment*.

        Later features override earlier ones.  If *platform* is given,
        target-specific dependencies are also merged in.
        """
        merged: dict[str, MatchSpec] = {}
        platform_keys = self.target_platform_keys(platform)
        for feature in self.resolve_features(environment):
            merged.update(feature.conda_dependencies)
            for key in platform_keys:
                if key in feature.target_conda_dependencies:
                    merged.update(feature.target_conda_dependencies[key])
        return merged

    def merged_pypi_dependencies(
        self,
        environment: Environment,
        platform: str | None = None,
    ) -> dict[str, PyPIDependency]:
        """Merge PyPI dependencies across features for *environment*."""
        merged: dict[str, PyPIDependency] = {}
        platform_keys = self.target_platform_keys(platform)
        for feature in self.resolve_features(environment):
            merged.update(feature.pypi_dependencies)
            for key in platform_keys:
                if key in feature.target_pypi_dependencies:
                    merged.update(feature.target_pypi_dependencies[key])
        return merged

    def merged_system_requirements(
        self,
        environment: Environment,
        platform: str | None = None,
    ) -> dict[str, str]:
        """Merge system requirements across features for *environment*."""
        merged: dict[str, str] = {}
        for feature in self.resolve_features(environment):
            merged.update(feature.system_requirements)
        if platform and platform in self.platform_system_requirements:
            merged.update(self.platform_system_requirements[platform])
        return merged

    def target_platform_keys(self, platform: str | None) -> tuple[str, ...]:
        """Return target table keys that apply to *platform* in merge order."""
        if platform is None:
            return ()
        subdir = self.platform_subdir(platform)
        if subdir == platform:
            return (platform,)
        return (subdir, platform)

    def platforms_for_toml(self) -> list[str | dict[str, str]]:
        """Return platforms in a TOML shape compatible with Pixi."""
        result: list[str | dict[str, str]] = []
        for platform in self.platforms:
            subdir = self.platform_subdir(platform)
            requirements = self.platform_system_requirements.get(platform)
            if not requirements:
                if platform == subdir:
                    result.append(platform)
                else:
                    result.append({"name": platform, "platform": subdir})
                continue
            entry = {"platform": subdir}
            if platform != self.synthesize_platform_name(subdir, requirements):
                entry["name"] = platform
            entry.update(
                {
                    self.platform_requirement_toml_aliases.get(name, name): value
                    for name, value in requirements.items()
                }
            )
            result.append(entry)
        return result

    def merged_channels(self, environment: Environment) -> list[Channel]:
        """Merge channels across features for *environment*.

        Feature-specific channels are appended after the workspace-level
        channels, preserving priority order.  Duplicates are removed.
        """
        seen: set[str] = set()
        result: list[Channel] = []
        for ch in self.channels:
            if ch.canonical_name not in seen:
                seen.add(ch.canonical_name)
                result.append(ch)
        for feature in self.resolve_features(environment):
            for ch in feature.channels:
                if ch.canonical_name not in seen:
                    seen.add(ch.canonical_name)
                    result.append(ch)
        return result


@dataclass
class TaskArg:
    """A named argument that can be passed to a task."""

    name: str
    default: str | None = None
    choices: list[str] | None = None

    def to_toml(self) -> dict[str, object]:
        """Serialize to a TOML-compatible dict."""
        entry: dict[str, object] = {"arg": self.name}
        if self.default is not None:
            entry["default"] = self.default
        if self.choices is not None:
            entry["choices"] = self.choices
        return entry


@dataclass
class TaskDependency:
    """A reference to another task that must run first."""

    task: str
    args: list[str | dict[str, str]] = field(default_factory=list)
    environment: str | None = None

    def to_toml(self) -> str | dict[str, object]:
        """Serialize to a TOML-compatible value (string or dict)."""
        if self.args or self.environment:
            entry: dict[str, object] = {"task": self.task}
            if self.args:
                entry["args"] = self.args
            if self.environment:
                entry["environment"] = self.environment
            return entry
        return self.task


@dataclass
class TaskOverride:
    """Per-platform override for any task field.

    Non-None fields replace the base task's values when the override
    is merged into a Task via ``Task.resolve_for_platform``.
    """

    cmd: str | list[str] | None = None
    args: list[TaskArg] | None = None
    depends_on: list[TaskDependency] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    inputs: list[str] | None = None
    outputs: list[str] | None = None
    clean_env: bool | None = None


@dataclass
class Task:
    """A single task definition with all its configuration."""

    name: str
    cmd: str | list[str] | None = None
    args: list[TaskArg] = field(default_factory=list)
    depends_on: list[TaskDependency] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    description: str | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    clean_env: bool = False
    default_environment: str | None = None
    platforms: dict[str, TaskOverride] | None = None

    @property
    def is_alias(self) -> bool:
        """True when the task is just a dependency grouping with no command."""
        return self.cmd is None and bool(self.depends_on)

    @property
    def is_hidden(self) -> bool:
        """Hidden tasks (prefixed with ``_``) are omitted from listings."""
        return self.name.startswith("_")

    def resolve_for_platform(self, subdir: str) -> Task:
        """Return a copy of this task with platform overrides merged in.

        *subdir* is a conda platform string such as ``linux-64`` or ``osx-arm64``.
        If there is no matching override the task is returned unchanged.
        """
        if not self.platforms or subdir not in self.platforms:
            return self

        override = self.platforms[subdir]
        kwargs: dict[str, Any] = {}
        for f in fields(self):
            if f.name in ("name", "platforms", "description", "default_environment"):
                kwargs[f.name] = getattr(self, f.name)
                continue
            override_val = (
                getattr(override, f.name, None) if hasattr(override, f.name) else None
            )
            if override_val is not None:
                kwargs[f.name] = override_val
            else:
                kwargs[f.name] = getattr(self, f.name)
        return Task(**kwargs)
