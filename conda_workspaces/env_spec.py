"""Environment specifier plugin for ``conda env create --file conda.toml``.

Registers a single environment specifier that handles ``conda.toml``
workspace manifests.  Parses the *default* environment's dependencies
and returns them as ``requested_packages`` so the solver can resolve
them.

The sibling lockfile specifier (:class:`~.lockfile.CondaLockLoader`)
handles ``conda.lock`` and lives alongside its write path in
:mod:`.lockfile`; the two modules mirror :mod:`.export` in that each
is the complete plugin surface for its format.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context
from conda.plugins.types import EnvironmentSpecBase

if TYPE_CHECKING:
    from typing import ClassVar, Final

    from conda.common.path import PathType
    from conda.models.environment import Environment
    from conda.models.match_spec import MatchSpec

#: Canonical plugin name for the ``conda.toml`` env spec.  Unversioned
#: because the manifest has no on-disk schema version of its own.
FORMAT: Final = "conda-workspaces"

#: User-friendly aliases.  No aliases yet; reserved for future rename.
ALIASES: Final = ()

#: Default filenames this plugin handles.
DEFAULT_FILENAMES: Final = ("conda.toml",)


class CondaWorkspaceSpec(EnvironmentSpecBase):
    """Parse a ``conda.toml`` workspace manifest for ``conda env create``.

    Returns the *default* environment's dependencies as
    ``requested_packages``, letting conda's solver resolve them.
    """

    detection_supported: ClassVar[bool] = True

    def __init__(self, path: PathType) -> None:
        self.path = Path(path).resolve()

    def can_handle(self) -> bool:
        if self.path.name not in DEFAULT_FILENAMES:
            return False
        if not self.path.exists():
            return False
        try:
            import tomlkit

            data = tomlkit.loads(self.path.read_text(encoding="utf-8"))
            return "workspace" in data
        except Exception:
            return False

    @property
    def env(self) -> Environment:
        """Return the default environment from the workspace manifest."""
        from conda.models.environment import Environment, EnvironmentConfig

        from .manifests import find_parser
        from .resolver import resolve_environment

        parser = find_parser(self.path)
        config = parser.parse(self.path)
        resolved = resolve_environment(config, "default", context.subdir)

        env_config = EnvironmentConfig(
            channels=tuple(ch.canonical_name for ch in resolved.channels),
        )

        requested: list[MatchSpec] = list(resolved.conda_dependencies.values())

        external: dict[str, list[str]] = {}
        if resolved.pypi_dependencies:
            external["pip"] = [str(dep) for dep in resolved.pypi_dependencies.values()]

        return Environment(
            name=config.name or self.path.parent.name,
            platform=context.subdir,
            config=env_config,
            requested_packages=requested,
            external_packages=external,
            variables=resolved.activation_env,
        )
