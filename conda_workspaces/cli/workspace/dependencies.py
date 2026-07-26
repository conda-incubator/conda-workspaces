"""Dependency declaration locations shared by workspace add and remove."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import tomlkit
from conda.utils import quote_for_shell
from tomlkit.items import InlineTable, Table

from ...exceptions import PlatformError
from ...models import Environment, Feature
from ...resolver import resolve_environment

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from ...models import WorkspaceConfig


@dataclass(frozen=True)
class DependencyLocation:
    """One explicit dependency table selected by CLI location options."""

    feature: str | None = None
    environment: str | None = None
    platform: str | None = None

    @classmethod
    def from_selectors(
        cls,
        *,
        feature: str | None,
        environment: str | None,
        platform: str | None,
    ) -> DependencyLocation:
        """Build a location while keeping the reserved default feature top-level."""
        return cls(
            feature=None if feature == Feature.DEFAULT_NAME else feature,
            environment=environment,
            platform=platform,
        )

    @property
    def path(self) -> tuple[str, ...]:
        """Return the TOML key path to this location."""
        if self.environment is not None:
            path = ("environments", self.environment)
        elif self.feature is not None:
            path = ("feature", self.feature)
        else:
            path = ()
        if self.platform is not None:
            path += ("target", self.platform)
        return path

    @property
    def display_name(self) -> str:
        """Return a short reader-facing location name."""
        if self.environment is not None:
            location = f"environment '{self.environment}'"
        elif self.feature is not None:
            location = f"feature '{self.feature}'"
        else:
            location = "default"
        if self.platform is not None:
            location += f" on platform '{self.platform}'"
        return location

    def table_name(
        self,
        dependency_key: str,
        namespace: tuple[str, ...] = (),
    ) -> str:
        """Return the concrete TOML dependency table name."""
        keys = (
            str(tomlkit.key(key)) for key in (*namespace, *self.path, dependency_key)
        )
        return f"[{'.'.join(keys)}]"

    def command(
        self,
        action: str,
        spec: str,
        *,
        pypi: bool,
        manifest_path: Path | None = None,
    ) -> str:
        """Return a shell-safe command selecting this location."""
        args = ["conda", "workspace"]
        if manifest_path is not None:
            args.extend(("--file", str(manifest_path)))
        args.append(action)
        if pypi:
            args.append("--pypi")
        if self.environment is not None:
            args.extend(("--environment", self.environment))
        elif self.feature is not None:
            args.extend(("--feature", self.feature))
        if self.platform is not None:
            args.extend(("--platform", self.platform))
        args.append(spec)
        return quote_for_shell(*args)

    def selector(self) -> str:
        """Return shell-safe CLI options selecting this location."""
        args: list[str] = []
        if self.environment is not None:
            args.extend(("--environment", self.environment))
        elif self.feature is not None:
            args.extend(("--feature", self.feature))
        if self.platform is not None:
            args.extend(("--platform", self.platform))
        return quote_for_shell(*args)

    def find_table(
        self,
        source: tomlkit.TOMLDocument | Table | InlineTable,
    ) -> tomlkit.TOMLDocument | Table | InlineTable | None:
        """Look up this location without changing the document."""
        current: Any = source
        for key in self.path:
            if not hasattr(current, "get"):
                return None
            current = current.get(key)
            if current is None:
                return None
        if isinstance(current, (tomlkit.TOMLDocument, Table, InlineTable)):
            return current
        return None

    def ensure_table(
        self,
        source: tomlkit.TOMLDocument | Table | InlineTable,
    ) -> tuple[tomlkit.TOMLDocument | Table | InlineTable, bool]:
        """Return this location, creating and normalizing its owner as needed."""
        created_environment = False
        if self.environment is not None:
            environments = ensure_child_table(source, "environments")
            definition = environments.get(self.environment)
            if isinstance(definition, (Table, InlineTable)):
                owner = definition
            else:
                owner = (
                    tomlkit.inline_table()
                    if isinstance(environments, InlineTable)
                    else tomlkit.table()
                )
                if isinstance(definition, list):
                    owner["features"] = list(definition)
                else:
                    created_environment = definition is None
                environments[self.environment] = owner
        elif self.feature is not None:
            features = ensure_child_table(source, "feature")
            definition = features.get(self.feature)
            if isinstance(definition, (Table, InlineTable)):
                owner = definition
            else:
                owner = (
                    tomlkit.inline_table()
                    if isinstance(features, InlineTable)
                    else tomlkit.table()
                )
                features[self.feature] = owner
        else:
            owner = source

        if self.platform is None:
            return owner, created_environment

        targets = ensure_child_table(owner, "target")
        definition = targets.get(self.platform)
        if isinstance(definition, (Table, InlineTable)):
            return definition, created_environment
        target = (
            tomlkit.inline_table()
            if isinstance(targets, InlineTable)
            else tomlkit.table()
        )
        targets[self.platform] = target
        return target, created_environment

    def validate_platform(
        self,
        config: WorkspaceConfig,
        source: tomlkit.TOMLDocument | Table | InlineTable,
        *,
        allow_existing: bool = False,
    ) -> None:
        """Reject a platform selector that cannot affect this workspace."""
        if self.platform is None:
            return
        if self.environment is not None:
            if self.environment in config.environments:
                available = set(resolve_environment(config, self.environment).platforms)
            else:
                prospective = replace(
                    config,
                    environments={
                        **config.environments,
                        self.environment: Environment(name=self.environment),
                    },
                )
                available = set(
                    resolve_environment(prospective, self.environment).platforms
                )
        elif self.feature is not None:
            available = set()
            for name, environment in config.environments.items():
                if self.feature in environment.features:
                    available.update(resolve_environment(config, name).platforms)
            if self.feature not in config.environments:
                prospective = replace(
                    config,
                    features={
                        **config.features,
                        self.feature: config.features.get(
                            self.feature,
                            Feature(name=self.feature),
                        ),
                    },
                    environments={
                        **config.environments,
                        self.feature: Environment(
                            name=self.feature,
                            features=[self.feature],
                        ),
                    },
                )
                available.update(
                    resolve_environment(prospective, self.feature).platforms
                )
        else:
            available = set(config.platforms)
            for name, environment in config.environments.items():
                if not environment.no_default_feature:
                    available.update(resolve_environment(config, name).platforms)
        available.update(config.platform_subdir(name) for name in tuple(available))
        if self.platform in available:
            return
        if allow_existing and self.has_declared_platform(source):
            return
        raise PlatformError(self.platform, sorted(available))

    def has_declared_platform(
        self,
        source: tomlkit.TOMLDocument | Table | InlineTable,
    ) -> bool:
        """Return whether this platform has a target table at any location."""
        if self.platform is None:
            return False
        owners: list[tomlkit.TOMLDocument | Table | InlineTable] = [source]
        for key in ("feature", "environments"):
            container = source.get(key)
            if isinstance(container, (Table, InlineTable)):
                owners.extend(
                    owner
                    for owner in container.values()
                    if isinstance(owner, (Table, InlineTable))
                )
        return any(
            isinstance(targets := owner.get("target"), (Table, InlineTable))
            and self.platform in targets
            for owner in owners
        )


@dataclass(frozen=True)
class DependencyDeclaration:
    """A package membership declaration and its dependency ecosystem."""

    location: DependencyLocation
    pypi: bool


def ensure_child_table(
    source: tomlkit.TOMLDocument | Table | InlineTable,
    key: str,
) -> Table | InlineTable:
    """Return a child table using the representation required by its parent."""
    child = source.get(key)
    if isinstance(child, (Table, InlineTable)):
        return child
    child = (
        tomlkit.inline_table() if isinstance(source, InlineTable) else tomlkit.table()
    )
    source[key] = child
    return child


def workspace_toml_source(
    document: tomlkit.TOMLDocument,
    manifest_path: Path,
    *,
    create: bool,
) -> tuple[tomlkit.TOMLDocument | Table | InlineTable | None, tuple[str, ...]]:
    """Return the workspace namespace selected by *manifest_path*."""
    if manifest_path.name != "pyproject.toml":
        return document, ()
    tool = ensure_child_table(document, "tool") if create else document.get("tool")
    if tool is None:
        return None, ()
    conda = tool.get("conda")
    if isinstance(conda, (Table, InlineTable)) and conda.get("workspace"):
        return conda, ("tool", "conda")
    pixi = tool.get("pixi")
    if isinstance(pixi, (Table, InlineTable)) and pixi.get("workspace"):
        return pixi, ("tool", "pixi")
    if create:
        return ensure_child_table(tool, "conda"), ("tool", "conda")
    return None, ()


def dependency_declarations(
    source: tomlkit.TOMLDocument | Table | InlineTable,
    name: str,
) -> list[DependencyDeclaration]:
    """Return all membership declarations named *name*, excluding the shared pool."""
    locations = [DependencyLocation()]
    targets = source.get("target")
    if isinstance(targets, (Table, InlineTable)):
        locations.extend(
            DependencyLocation(platform=platform) for platform in sorted(targets)
        )

    features = source.get("feature")
    if isinstance(features, (Table, InlineTable)):
        for feature in sorted(features):
            owner = features[feature]
            locations.append(DependencyLocation(feature=feature))
            if not isinstance(owner, (Table, InlineTable)):
                continue
            targets = owner.get("target")
            if isinstance(targets, (Table, InlineTable)):
                locations.extend(
                    DependencyLocation(feature=feature, platform=platform)
                    for platform in sorted(targets)
                )

    environments = source.get("environments")
    if isinstance(environments, (Table, InlineTable)):
        for environment in sorted(environments):
            owner = environments[environment]
            locations.append(DependencyLocation(environment=environment))
            if not isinstance(owner, (Table, InlineTable)):
                continue
            targets = owner.get("target")
            if isinstance(targets, (Table, InlineTable)):
                locations.extend(
                    DependencyLocation(environment=environment, platform=platform)
                    for platform in sorted(targets)
                )

    declarations: list[DependencyDeclaration] = []
    for location in locations:
        table = location.find_table(source)
        if table is None:
            continue
        for pypi, key in ((False, "dependencies"), (True, "pypi-dependencies")):
            if name in table.get(key, {}):
                declarations.append(DependencyDeclaration(location, pypi))
    return declarations


def effective_dependency_location(
    source: tomlkit.TOMLDocument | Table | InlineTable,
    config: WorkspaceConfig,
    environment: Environment,
    platform: str,
    dependency_key: str,
    name: str,
) -> DependencyLocation | None:
    """Return the last declaration that wins for one environment and platform."""
    chain: list[DependencyLocation] = []
    platform_keys = config.target_platform_keys(platform)
    for feature in config.resolve_features(environment):
        feature_name = None if feature.is_default else feature.name
        chain.append(DependencyLocation(feature=feature_name))
        chain.extend(
            DependencyLocation(feature=feature_name, platform=key)
            for key in platform_keys
        )
    chain.append(DependencyLocation(environment=environment.name))
    chain.extend(
        DependencyLocation(environment=environment.name, platform=key)
        for key in platform_keys
    )

    winner = None
    for location in chain:
        table = location.find_table(source)
        if table is not None and name in table.get(dependency_key, {}):
            winner = location
    return winner
