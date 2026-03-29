"""Feature-to-environment resolver.

Takes a ``WorkspaceConfig`` and resolves which conda/PyPI packages
need to be installed for a given environment by composing its
constituent features.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .exceptions import (
    PlatformError,
)

if TYPE_CHECKING:
    from .models import Channel, MatchSpec, PyPIDependency, WorkspaceConfig

log = logging.getLogger(__name__)


@dataclass
class ResolvedEnvironment:
    """The fully resolved dependency set for a single environment.

    This is what the environment manager uses to install or update
    a project-local conda environment.
    """

    name: str
    conda_dependencies: dict[str, MatchSpec] = field(default_factory=dict)
    pypi_dependencies: dict[str, PyPIDependency] = field(default_factory=dict)
    channels: list[Channel] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    activation_scripts: list[str] = field(default_factory=list)
    activation_env: dict[str, str] = field(default_factory=dict)
    system_requirements: dict[str, str] = field(default_factory=dict)
    channel_priority: str | None = None


def resolve_environment(
    config: WorkspaceConfig,
    env_name: str,
    platform: str | None = None,
) -> ResolvedEnvironment:
    """Resolve an environment by composing its features.

    Merges conda deps, PyPI deps, channels, activation scripts/env,
    and system requirements across all features in the environment.

    If *platform* is given, target-specific overrides are included
    and platform support is validated.
    """
    env = config.get_environment(env_name)

    # Validate platform if provided
    if platform and config.platforms and platform not in config.platforms:
        raise PlatformError(platform, config.platforms)

    resolved = ResolvedEnvironment(
        name=env_name,
        channel_priority=config.channel_priority,
    )

    # Merge dependencies
    resolved.conda_dependencies = config.merged_conda_dependencies(env, platform)
    resolved.pypi_dependencies = config.merged_pypi_dependencies(env, platform)
    resolved.channels = config.merged_channels(env)

    # Merge platforms: intersect feature platforms with workspace platforms
    feature_platforms: set[str] = set()
    features = config.resolve_features(env)
    for feat in features:
        if feat.platforms:
            if not feature_platforms:
                feature_platforms = set(feat.platforms)
            else:
                feature_platforms &= set(feat.platforms)

    if feature_platforms:
        resolved.platforms = sorted(feature_platforms)
    else:
        if any(f.platforms for f in features):
            log.warning(
                "Feature platform intersection for environment '%s' is empty; "
                "falling back to workspace platforms",
                env_name,
            )
        resolved.platforms = list(config.platforms)

    # Merge activation and system requirements
    for feat in features:
        resolved.activation_scripts.extend(feat.activation_scripts)
        resolved.activation_env.update(feat.activation_env)
        resolved.system_requirements.update(feat.system_requirements)

    return resolved


def resolve_all_environments(
    config: WorkspaceConfig,
    platform: str | None = None,
) -> dict[str, ResolvedEnvironment]:
    """Resolve all environments in the workspace.

    Returns a dict mapping environment name to its resolved deps.
    """
    return {
        name: resolve_environment(config, name, platform)
        for name in config.environments
    }
