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

from dataclasses import dataclass, field
from typing import ClassVar

from conda.base.constants import KNOWN_SUBDIRS
from conda.models.channel import Channel  # noqa: TC002
from conda.models.match_spec import MatchSpec  # noqa: TC002

from .exceptions import (
    EnvironmentNotFoundError,
    FeatureNotFoundError,
    PlatformError,
)


@dataclass(frozen=True)
class PyPIDependency:
    """A PyPI dependency (PEP 508 string).

    Names are translated to conda equivalents via ``conda-pypi``'s
    grayskull mapping and merged into the same solver call as conda
    deps.  Path/git/URL deps are handled post-solve via conda-pypi's
    build system.
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

    def __post_init__(self) -> None:
        """Ensure the default feature and environment always exist.

        Also validates that all declared platforms are recognised
        conda subdirs (e.g. ``linux-64``, ``osx-arm64``).
        """
        if Feature.DEFAULT_NAME not in self.features:
            self.features[Feature.DEFAULT_NAME] = Feature(name=Feature.DEFAULT_NAME)
        if Environment.DEFAULT_NAME not in self.environments:
            self.environments[Environment.DEFAULT_NAME] = Environment(
                name=Environment.DEFAULT_NAME
            )

        invalid = [p for p in self.platforms if p not in KNOWN_SUBDIRS]
        if invalid:
            raise PlatformError(
                ", ".join(invalid),
                sorted(KNOWN_SUBDIRS),
            )

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
        for feature in self.resolve_features(environment):
            merged.update(feature.conda_dependencies)
            if platform and platform in feature.target_conda_dependencies:
                merged.update(feature.target_conda_dependencies[platform])
        return merged

    def merged_pypi_dependencies(
        self,
        environment: Environment,
        platform: str | None = None,
    ) -> dict[str, PyPIDependency]:
        """Merge PyPI dependencies across features for *environment*."""
        merged: dict[str, PyPIDependency] = {}
        for feature in self.resolve_features(environment):
            merged.update(feature.pypi_dependencies)
            if platform and platform in feature.target_pypi_dependencies:
                merged.update(feature.target_pypi_dependencies[platform])
        return merged

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
