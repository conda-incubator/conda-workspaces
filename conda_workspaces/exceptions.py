"""Exception hierarchy for conda-workspaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.exceptions import CondaError

if TYPE_CHECKING:
    from pathlib import Path


class CondaWorkspacesError(CondaError):
    """Base exception for all conda-workspaces errors.

    Subclasses provide *hints* — actionable suggestions shown below
    the main error message.  The full ``str(exc)`` still contains
    everything (for conda's fallback handler), but the Rich renderer
    in ``main.py`` uses ``error_message`` and ``hints`` separately.
    """

    error_message: str
    hints: list[str]

    def __init__(
        self,
        message: str,
        *,
        hints: list[str] | None = None,
    ) -> None:
        self.error_message = message
        self.hints = hints or []
        full = message
        if self.hints:
            full += "\n" + "\n".join(self.hints)
        super().__init__(full)


class WorkspaceNotFoundError(CondaWorkspacesError):
    """No workspace manifest was found in *search_dir* or its parents."""

    def __init__(self, search_dir: str | Path) -> None:
        self.search_dir = search_dir
        super().__init__(
            f"No workspace manifest found in '{search_dir}' or any parent directory.",
            hints=[
                "Create a conda.toml, pixi.toml, or pyproject.toml"
                " (with [tool.conda.workspace]) to define a workspace.",
            ],
        )


class WorkspaceParseError(CondaWorkspacesError):
    """The workspace manifest could not be parsed."""

    def __init__(self, path: str | Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"Failed to parse workspace manifest '{path}': {reason}",
            hints=["Check the file syntax and try again."],
        )


class EnvironmentNotFoundError(CondaWorkspacesError):
    """The requested environment is not defined in the workspace."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        hints = []
        if available:
            hints.append(f"Available environments: {', '.join(sorted(available))}")
        super().__init__(
            f"Environment '{name}' is not defined in the workspace.",
            hints=hints,
        )


class EnvironmentNotInstalledError(CondaWorkspacesError):
    """The requested environment exists but is not installed."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"Environment '{name}' is not installed.",
            hints=[f"Run 'conda workspace install -e {name}' first."],
        )


class ManifestExistsError(CondaWorkspacesError):
    """A workspace manifest already exists at the target path."""

    def __init__(self, path: str | Path) -> None:
        self.path = path
        super().__init__(
            f"'{path}' already exists.",
            hints=["Use a different format or location."],
        )


class FeatureNotFoundError(CondaWorkspacesError):
    """A feature referenced by an environment does not exist."""

    def __init__(self, feature: str, environment: str) -> None:
        self.feature = feature
        self.environment = environment
        super().__init__(
            f"Feature '{feature}' referenced by environment '{environment}'"
            " is not defined in the workspace.",
            hints=[
                f"Add [feature.{feature}.dependencies] to your manifest.",
            ],
        )


class PlatformError(CondaWorkspacesError):
    """Platform configuration error."""

    def __init__(self, platform: str, available: list[str]) -> None:
        self.platform = platform
        self.available = available
        super().__init__(
            f"Platform '{platform}' is not supported by this workspace.",
            hints=[
                f"Supported platforms: {', '.join(sorted(available))}",
            ],
        )


class SolveError(CondaWorkspacesError):
    """Dependency solving failed for an environment, optionally scoped to a platform."""

    def __init__(
        self,
        environment: str,
        reason: str,
        *,
        platform: str | None = None,
    ) -> None:
        self.environment = environment
        self.reason = reason
        self.platform = platform
        target = (
            f"environment '{environment}' for platform '{platform}'"
            if platform
            else f"environment '{environment}'"
        )
        super().__init__(
            f"Failed to solve {target}: {reason}",
            hints=["Check your dependency specifications and channel configuration."],
        )


class AllTargetsUnsolvableError(CondaWorkspacesError):
    """Every ``(environment, platform)`` pair failed under ``--skip-unsolvable``.

    Raised after the loop when at least one pair failed to solve *and*
    no pair succeeded, so writing a lockfile would produce an empty
    file that silently loses every environment.
    """

    def __init__(self, failures: list[SolveError]) -> None:
        self.failures = failures
        summary = "\n".join(
            f"  - {failure.environment}"
            + (f" on {failure.platform}" if failure.platform else "")
            + f": {failure.reason}"
            for failure in failures
        )
        super().__init__(
            "Every (environment, platform) pair failed to solve:\n" + summary,
            hints=[
                "Fix at least one pair, or re-run without --skip-unsolvable"
                " to see a single fail-fast error.",
            ],
        )


class ActivationError(CondaWorkspacesError):
    """Environment activation failed."""

    def __init__(self, environment: str, reason: str) -> None:
        self.environment = environment
        self.reason = reason
        super().__init__(
            f"Failed to activate environment '{environment}': {reason}",
            hints=[
                f"Ensure the environment is installed:"
                f" conda workspace install -e {environment}",
            ],
        )


class LockfileNotFoundError(CondaWorkspacesError):
    """No lockfile or lockfile entry exists for the requested environment."""

    def __init__(self, environment: str, path: str | Path) -> None:
        self.environment = environment
        self.path = path
        super().__init__(
            f"No lockfile entry found for environment '{environment}' in {path}.",
            hints=["Run 'conda workspace install' to generate one."],
        )


class LockfileStaleError(CondaWorkspacesError):
    """The lockfile is older than the workspace manifest."""

    def __init__(self, manifest: str | Path, lockfile: str | Path) -> None:
        self.manifest = manifest
        self.lockfile = lockfile
        super().__init__(
            f"Lockfile '{lockfile}' is out of date"
            f" (manifest '{manifest}' has been modified since the last lock).",
            hints=[
                "Run 'conda workspace lock' to update it,"
                " or use --frozen to install anyway.",
            ],
        )


class TaskNotFoundError(CondaWorkspacesError):
    """Raised when a referenced task does not exist."""

    def __init__(self, task_name: str, available: list[str] | None = None) -> None:
        hints = []
        if available:
            hints.append(f"Available tasks: {', '.join(sorted(available))}")
        super().__init__(f"Task '{task_name}' not found.", hints=hints)


class CyclicDependencyError(CondaWorkspacesError):
    """Raised when the task dependency graph contains a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        path = " -> ".join(cycle)
        super().__init__(
            f"Cyclic dependency detected: {path}",
            hints=["Remove or restructure the circular depends-on references."],
        )


class TaskParseError(CondaWorkspacesError):
    """Raised when a task definition file cannot be parsed."""

    def __init__(self, path: str | Path, reason: str) -> None:
        super().__init__(
            f"Failed to parse '{path}': {reason}",
            hints=["Check the file syntax and try again."],
        )


class TaskExecutionError(CondaWorkspacesError):
    """Raised when a task command exits with a non-zero status."""

    def __init__(self, task_name: str, exit_code: int) -> None:
        super().__init__(
            f"Task '{task_name}' failed with exit code {exit_code}.",
        )


class NoTaskFileError(CondaWorkspacesError):
    """Raised when no task definition file is found."""

    def __init__(self, search_dir: str) -> None:
        super().__init__(
            f"No task file found in '{search_dir}'.",
            hints=[
                "Create a conda.toml, pixi.toml, or pyproject.toml"
                " with task definitions.",
            ],
        )
