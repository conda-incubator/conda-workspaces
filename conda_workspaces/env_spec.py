"""Environment specifier plugins for ``conda env create --file``.

Registers two environment specifiers:

``conda-workspaces``
    Handles ``conda.toml`` workspace manifests.  Parses the default
    environment's dependencies and returns them as
    ``requested_packages`` so the solver can resolve them.

``conda-workspaces-lock``
    Handles ``conda.lock`` files.  Returns ``explicit_packages`` so
    ``conda env create`` installs exact URLs without solving.

These allow the standard conda workflow::

    conda env create --file conda.toml -n myenv
    conda env create --file conda.lock -n myenv
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context
from conda.plugins.types import EnvironmentSpecBase

if TYPE_CHECKING:
    from typing import Any, ClassVar

    from conda.common.path import PathType
    from conda.models.environment import Environment
    from conda.models.match_spec import MatchSpec

#: Filenames the workspace env spec can handle.
WORKSPACE_FILENAMES = {"conda.toml"}

#: Filenames the lockfile env spec can handle.
LOCK_FILENAMES = {"conda.lock"}


class CondaWorkspaceSpec(EnvironmentSpecBase):
    """Parse a ``conda.toml`` workspace manifest for ``conda env create``.

    Returns the *default* environment's dependencies as
    ``requested_packages``, letting conda's solver resolve them.
    """

    detection_supported: ClassVar[bool] = True

    def __init__(self, path: PathType) -> None:
        self.path = Path(path).resolve()

    def can_handle(self) -> bool:
        if self.path.name not in WORKSPACE_FILENAMES:
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

        from .parsers import find_parser
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


class CondaLockSpec(EnvironmentSpecBase):
    """Parse a ``conda.lock`` file for ``conda env create``.

    Returns ``explicit_packages`` so the solver is bypassed entirely.
    """

    detection_supported: ClassVar[bool] = True

    def __init__(self, path: PathType) -> None:
        self.path = Path(path).resolve()
        self._data_cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data_cache is None:
            from conda.common.serialize.yaml import load as yaml_load

            with self.path.open(encoding="utf-8") as fh:
                self._data_cache = yaml_load(fh)
        return self._data_cache

    def can_handle(self) -> bool:
        if self.path.name not in LOCK_FILENAMES:
            return False
        if not self.path.exists():
            return False
        try:
            data = self._load()
            return data.get("version") == 1
        except Exception:
            return False

    @property
    def env(self) -> Environment:
        """Return the default environment from the lockfile."""
        from conda.common.io import dashlist
        from conda.models.channel import Channel
        from conda.models.environment import Environment, EnvironmentConfig
        from conda_lockfiles.records_from_conda_urls import records_from_conda_urls

        from .lockfile import LOCKFILE_VERSION

        data = self._load()
        version = data.get("version")
        if version != LOCKFILE_VERSION:
            raise ValueError(f"Unsupported lockfile version: {version}")

        environments = data.get("environments", {})
        env_name = "default"
        platform = context.subdir

        if env_name not in environments:
            available = sorted(environments)
            raise ValueError(
                f"Environment '{env_name}' not found in lockfile. "
                f"Available environments: {dashlist(available)}"
            )

        env_data = environments[env_name]
        packages_by_platform = env_data.get("packages", {})
        if platform not in packages_by_platform:
            available = sorted(packages_by_platform)
            raise ValueError(
                f"Lockfile does not list packages for platform {platform}. "
                f"Available platforms: {dashlist(available)}."
            )

        # Build channel config from the lockfile
        channels = env_data.get("channels", [])
        env_config = EnvironmentConfig(
            channels=tuple(Channel(ch["url"]).canonical_name for ch in channels),
        )

        # Build package lookup from the top-level packages list
        all_packages = data.get("packages", [])
        lookup: dict[str, dict[str, Any]] = {}
        for pkg in all_packages:
            url = pkg.get("conda")
            if url:
                lookup[url] = pkg

        # Extract explicit packages and external packages
        metadata_by_url: dict[str, dict[str, Any]] = {}
        external_packages: dict[str, list[str]] = {}

        for ref in packages_by_platform[platform]:
            if "conda" in ref:
                url = ref["conda"]
                meta = lookup.get(url, {})
                metadata_by_url[url] = {
                    k: meta[k] for k in ("sha256", "md5") if k in meta
                }
            elif "pypi" in ref:
                external_packages.setdefault("pip", []).append(ref["pypi"])

        records = list(records_from_conda_urls(metadata_by_url))

        return Environment(
            name=env_name,
            platform=platform,
            config=env_config,
            explicit_packages=records,
            external_packages=external_packages,
        )
