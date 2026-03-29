"""Exception hierarchy for conda-workspaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.exceptions import CondaError

if TYPE_CHECKING:
    from pathlib import Path


class CondaWorkspacesError(CondaError):
    """Base exception for all conda-workspaces errors."""


class WorkspaceNotFoundError(CondaWorkspacesError):
    """No workspace manifest was found in *search_dir* or its parents."""

    def __init__(self, search_dir: str | Path) -> None:
        self.search_dir = search_dir
        super().__init__(
            f"No workspace manifest found in '{search_dir}' or any parent directory.\n"
            "Create a conda.toml, pixi.toml, or pyproject.toml "
            "(with [tool.conda.workspace]) to define a workspace."
        )


class WorkspaceParseError(CondaWorkspacesError):
    """The workspace manifest could not be parsed."""

    def __init__(self, path: str | Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to parse workspace manifest '{path}': {reason}")


class EnvironmentNotFoundError(CondaWorkspacesError):
    """The requested environment is not defined in the workspace."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        hint = ""
        if available:
            hint = f"\nAvailable environments: {', '.join(sorted(available))}"
        super().__init__(f"Environment '{name}' is not defined in the workspace.{hint}")


class EnvironmentNotInstalledError(CondaWorkspacesError):
    """The requested environment exists but is not installed."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"Environment '{name}' is not installed.\n"
            f"Run 'conda workspace install -e {name}' first."
        )


class ManifestExistsError(CondaWorkspacesError):
    """A workspace manifest already exists at the target path."""

    def __init__(self, path: str | Path) -> None:
        self.path = path
        super().__init__(
            f"'{path}' already exists. Use a different format or location."
        )


class FeatureNotFoundError(CondaWorkspacesError):
    """A feature referenced by an environment does not exist."""

    def __init__(self, feature: str, environment: str) -> None:
        self.feature = feature
        self.environment = environment
        super().__init__(
            f"Feature '{feature}' referenced by environment '{environment}' "
            "is not defined in the workspace."
        )


class ChannelError(CondaWorkspacesError):
    """Channel configuration error."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PlatformError(CondaWorkspacesError):
    """Platform configuration error."""

    def __init__(self, platform: str, available: list[str]) -> None:
        self.platform = platform
        self.available = available
        super().__init__(
            f"Platform '{platform}' is not supported by this workspace.\n"
            f"Supported platforms: {', '.join(sorted(available))}"
        )


class SolveError(CondaWorkspacesError):
    """Dependency solving failed for an environment."""

    def __init__(self, environment: str, reason: str) -> None:
        self.environment = environment
        self.reason = reason
        super().__init__(f"Failed to solve environment '{environment}': {reason}")


class ActivationError(CondaWorkspacesError):
    """Environment activation failed."""

    def __init__(self, environment: str, reason: str) -> None:
        self.environment = environment
        self.reason = reason
        super().__init__(f"Failed to activate environment '{environment}': {reason}")


class LockfileNotFoundError(CondaWorkspacesError):
    """No lockfile or lockfile entry exists for the requested environment."""

    def __init__(self, environment: str, path: str | Path) -> None:
        self.environment = environment
        self.path = path
        super().__init__(
            f"No lockfile entry found for environment '{environment}' "
            f"in {path}.\n"
            f"Run 'conda workspace install' to generate one."
        )


class LockfileStaleError(CondaWorkspacesError):
    """The lockfile is older than the workspace manifest."""

    def __init__(self, manifest: str | Path, lockfile: str | Path) -> None:
        self.manifest = manifest
        self.lockfile = lockfile
        super().__init__(
            f"Lockfile '{lockfile}' is out of date "
            f"(manifest '{manifest}' has been modified since the last lock).\n"
            "Run 'conda workspace lock' to update it, "
            "or use --frozen to install anyway."
        )
