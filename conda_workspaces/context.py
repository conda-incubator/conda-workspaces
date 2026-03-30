"""Workspace context — lazy properties for conda & workspace state.

Provides a namespace of lazily-evaluated properties that downstream
code can use without importing conda at module level.  This keeps
import-time overhead negligible.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import WorkspaceConfig


class WorkspaceContext:
    """Lazy-evaluated context for the current workspace.

    Properties are resolved on first access and cached.  Conda imports
    are deferred to keep plugin load time under 1 ms.
    """

    def __init__(self, config: WorkspaceConfig | None = None) -> None:
        self._config = config
        self._cache: dict[str, object] = {}

    @property
    def config(self) -> WorkspaceConfig:
        """The parsed workspace configuration."""
        if self._config is None:
            from .parsers import detect_and_parse

            _, self._config = detect_and_parse()
        return self._config

    @property
    def root(self) -> Path:
        """Workspace root directory."""
        return Path(self.config.root)

    @property
    def envs_dir(self) -> Path:
        """Directory where project-local environments are stored."""
        return self.root / self.config.envs_dir

    @property
    def platform(self) -> str:
        """Current conda subdir (e.g. ``osx-arm64``)."""
        if "platform" not in self._cache:
            from conda.base.context import context

            self._cache["platform"] = context.subdir
        return self._cache["platform"]  # type: ignore[return-value]

    @property
    def root_prefix(self) -> Path:
        """Conda root prefix (base environment)."""
        if "root_prefix" not in self._cache:
            from conda.base.context import context

            self._cache["root_prefix"] = Path(context.root_prefix)
        return self._cache["root_prefix"]  # type: ignore[return-value]

    def env_prefix(self, env_name: str) -> Path:
        """Return the prefix path for a named environment."""
        return self.envs_dir / env_name

    def env_exists(self, env_name: str) -> bool:
        """Check whether the prefix is a valid conda environment."""
        from conda.core.envs_manager import PrefixData

        prefix = self.env_prefix(env_name)
        return PrefixData(str(prefix)).is_environment()


class CondaContext:
    """Lazy-evaluated namespace exposed as ``conda.*`` in task templates.

    Attribute access is deferred so conda internals load only when a
    template references a variable.
    """

    def __init__(self, manifest_path: Path | None = None) -> None:
        self._manifest_path = manifest_path

    @property
    def platform(self) -> str:
        """The conda platform/subdir string, e.g. ``linux-64`` or ``osx-arm64``."""
        from conda.base.context import context

        return context.subdir

    @property
    def environment_name(self) -> str:
        """Name of the currently active conda environment, or ``"base"``."""
        from conda.base.context import context

        if context.active_prefix:
            return Path(context.active_prefix).name
        return "base"

    @property
    def environment(self) -> _EnvironmentProxy:
        """Allows ``{{ conda.environment.name }}`` in templates."""
        return _EnvironmentProxy(self.environment_name)

    @property
    def prefix(self) -> str:
        """Absolute path to the target conda environment prefix."""
        from conda.base.context import context

        return str(context.target_prefix)

    @property
    def version(self) -> str:
        """The installed conda version string."""
        from conda import __version__

        return __version__

    @property
    def manifest_path(self) -> str:
        """Path to the task definition file, or empty string if unknown."""
        return str(self._manifest_path) if self._manifest_path else ""

    @property
    def init_cwd(self) -> str:
        """The working directory at the time of context creation."""
        return os.getcwd()

    @property
    def is_win(self) -> bool:
        """True when running on Windows."""
        from conda.base.constants import on_win

        return on_win

    @property
    def is_unix(self) -> bool:
        """True when running on a Unix-like system (Linux or macOS)."""
        from conda.base.constants import on_win

        return not on_win

    @property
    def is_osx(self) -> bool:
        """True when the host platform is macOS."""
        from conda.base.context import context

        return context.platform == "osx"

    @property
    def is_linux(self) -> bool:
        """True when the host platform is Linux."""
        from conda.base.context import context

        return context.platform == "linux"


class _EnvironmentProxy:
    """Allows ``{{ conda.environment.name }}`` in templates."""

    def __init__(self, name: str) -> None:
        self.name = name


def build_template_context(
    manifest_path: Path | None = None,
    task_args: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build the full Jinja2 template context dict.

    The returned dict contains:
    - ``conda``: a :class:`CondaContext` instance
    - ``pixi``: alias to the same context (for pixi.toml compatibility)
    - Any user-supplied task argument values
    """
    ctx = CondaContext(manifest_path=manifest_path)
    result: dict[str, object] = {"conda": ctx, "pixi": ctx}
    if task_args:
        result.update(task_args)
    return result
