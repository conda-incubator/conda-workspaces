"""Workspace context — lazy properties for conda & workspace state.

Provides a namespace of lazily-evaluated properties that downstream
code can use without importing conda at module level.  This keeps
import-time overhead negligible.
"""

from __future__ import annotations

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

    @property
    def is_platform_supported(self) -> bool:
        """Whether the current platform is in the workspace's platform list."""
        if not self.config.platforms:
            return True
        return self.platform in self.config.platforms

    def env_prefix(self, env_name: str) -> Path:
        """Return the prefix path for a named environment."""
        return self.envs_dir / env_name

    def env_exists(self, env_name: str) -> bool:
        """Check whether the prefix is a valid conda environment."""
        from conda.core.envs_manager import PrefixData

        prefix = self.env_prefix(env_name)
        return PrefixData(str(prefix)).is_environment()
