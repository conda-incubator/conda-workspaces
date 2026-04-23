"""Lockfile generation, consumption and env-spec plugin for ``conda.lock``.

Single source of truth for every ``conda.lock`` concern.  The file owns
the read path, the write path, the ``CondaEnvironmentSpecifier`` plugin
class (:class:`CondaLockLoader`), and the plugin metadata (name,
aliases, default filename) consumed by ``plugin.py`` and
:mod:`.export`.

The ``conda.lock`` format is a *derivative* of rattler-lock v6
(``pixi.lock``): same schema machinery, same top-level keys
(``version``, ``environments``, ``packages``), but with an on-disk
``version: 1`` byte that identifies the file as conda-workspaces-owned.
:class:`CondaLockLoader` shares rattler-lock v6 conversion logic with
:mod:`conda_lockfiles.rattler_lock.v6` via an in-memory ``version: 6``
swap, so we do not re-implement YAML -> ``Environment`` conversion.

The file layout is::

    version: 1
    environments:
      <name>:
        channels: [{url: ...}, ...]
        packages:
          <platform>: [{conda: <url>}, ...]
    packages:
      - conda: <url>
        sha256: ...
        md5: ...
        depends: [...]
        ...

On the *write* side, :func:`generate_lockfile` solves each environment
and delegates YAML serialisation to the ``multiplatform_export`` hook
in :mod:`.export` (the same path ``conda export`` uses), so every
``conda.lock`` on disk comes out of a single formatter.  On the *read*
side, :func:`install_from_lockfile` extracts the package list for one
environment + platform via :class:`CondaLockLoader` and installs the
exact URLs, bypassing the solver entirely.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from conda.plugins.types import EnvironmentSpecBase

from .exceptions import AllTargetsUnsolvableError, LockfileNotFoundError, SolveError

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, ClassVar, Final

    from conda.common.path import PathType
    from conda.models.environment import Environment

    from .context import WorkspaceContext
    from .resolver import ResolvedEnvironment

#: On-disk lockfile format version.  Distinct from the rattler-lock v6
#: schema version so that tools can tell a conda-workspaces-owned lock
#: from a pixi-owned one at a glance.
LOCKFILE_VERSION: Final = 1

#: The canonical lockfile filename.
LOCKFILE_NAME: Final = "conda.lock"

#: Canonical, versioned plugin name.  Stable across schema bumps;
#: follows the ``conda-lockfiles`` alias policy (see
#: ``docs/reference/format-aliases.md``).
FORMAT: Final = "conda-workspaces-lock-v1"

#: User-friendly aliases.  Unversioned names are convenience handles
#: that may migrate to a newer ``FORMAT`` in the future.
ALIASES: Final = ("conda-workspaces-lock", "workspace-lock")

#: Default filenames this plugin handles.
DEFAULT_FILENAMES: Final = (LOCKFILE_NAME,)


def lockfile_path(ctx: WorkspaceContext) -> Path:
    """Return the path to the workspace lockfile (``<root>/conda.lock``)."""
    return ctx.root / LOCKFILE_NAME


class CondaLockLoader(EnvironmentSpecBase):
    """Environment specifier + loader for ``conda.lock``.

    ``conda.lock`` is a derivative of rattler-lock v6 (``pixi.lock``);
    this loader shares the rattler-lock v6 conversion helper from
    :mod:`conda_lockfiles.rattler_lock.v6` by performing an in-memory
    ``version: 1 -> 6`` swap before handing the data off.  The on-disk
    file keeps ``version: 1`` unchanged.

    Used by ``conda env create --file conda.lock`` (single platform via
    ``env``) and by ``conda workspace install`` (multi-platform via
    ``env_for``).
    """

    detection_supported: ClassVar[bool] = True

    def __init__(self, path: PathType) -> None:
        self.path = Path(path).resolve()
        self._data_cache: dict[str, Any] | None = None

    def can_handle(self) -> bool:
        if self.path.name not in DEFAULT_FILENAMES:
            return False
        if not self.path.exists():
            return False
        try:
            return self._data.get("version") == LOCKFILE_VERSION
        except Exception:
            return False

    @property
    def _data(self) -> dict[str, Any]:
        if self._data_cache is None:
            from conda_lockfiles.load_yaml import load_yaml

            self._data_cache = load_yaml(self.path)
        return self._data_cache

    @property
    def available_platforms(self) -> tuple[str, ...]:
        """Platforms declared in this lockfile's default environment."""
        env_data = self._env_data("default")
        return tuple(sorted(env_data.get("packages", {})))

    def env_for(self, platform: str, name: str = "default") -> Environment:
        """Return the conda ``Environment`` for *platform* and *name*.

        Raises ``ValueError`` if *platform* is not in the lockfile or
        *name* does not identify a declared environment.
        """
        # TODO: raise conda.exceptions.PlatformMismatchError once that
        # lands in a released conda (tracked in conda/conda#15928).
        env_data = self._env_data(name)
        platforms = tuple(sorted(env_data.get("packages", {})))
        if platform not in platforms:
            from conda.common.io import dashlist

            raise ValueError(
                f"Lockfile does not list packages for platform {platform!r}. "
                f"Available platforms: {dashlist(platforms)}."
            )

        # Share rattler-lock v6 conversion with conda-lockfiles via a
        # localised in-memory version byte swap.  Disk file is untouched.
        # Shallow copy is sufficient: we only overwrite the top-level
        # ``version`` key; nested structures stay shared with the cache
        # and must not be mutated by the upstream helper.
        #
        # TODO: switch to the public ``rattler_lock_v6_to_conda_env`` +
        # ``RattlerLockV6`` pydantic model once conda-lockfiles ships
        # the APIs added in conda-incubator/conda-lockfiles#128.  The
        # current private helper is stable across the 0.1.x line but is
        # not part of the public contract.
        from conda_lockfiles.rattler_lock.v6 import _rattler_lock_v6_to_env

        payload = dict(self._data)
        payload["version"] = 6
        return _rattler_lock_v6_to_env(name=name, platform=platform, **payload)

    @property
    def env(self) -> Environment:
        """Return the default environment for the current subdir.

        Kept for backwards compatibility with ``conda env create --file
        conda.lock``; delegates to :meth:`env_for`.
        """
        from conda.base.context import context

        return self.env_for(context.subdir)

    def _env_data(self, name: str = "default") -> dict[str, Any]:
        data = self._data
        if data.get("version") != LOCKFILE_VERSION:
            raise ValueError(
                f"Unsupported {LOCKFILE_NAME} version: {data.get('version')!r} "
                f"(expected {LOCKFILE_VERSION})"
            )
        environments = data.get("environments", {})
        if name not in environments:
            from conda.common.io import dashlist

            raise ValueError(
                f"Environment {name!r} not found in lockfile. "
                f"Available environments: {dashlist(sorted(environments))}"
            )
        return environments[name]


def generate_lockfile(
    ctx: WorkspaceContext,
    resolved_envs: dict[str, ResolvedEnvironment],
    *,
    platforms: tuple[str, ...] | None = None,
    progress: Callable[[str, str], None] | None = None,
    skip_unsolvable: bool = False,
    on_skip: Callable[[str, str, SolveError], None] | None = None,
) -> Path:
    """Generate a ``conda.lock`` by solving workspace environments.

    Solves each environment in *resolved_envs* for every platform it
    declares (intersected with *platforms* when given) and writes the
    results to ``<workspace>/conda.lock``.  Serialisation is delegated
    to :func:`.export.multiplatform_export` so this function and
    ``conda export --format=conda-workspaces-lock-v1`` produce
    byte-identical output.  Solver chatter is silenced inside
    :meth:`ResolvedEnvironment.solve_for_platform` itself, so the
    caller is free to render status through the optional *progress*
    callback without stdout bookkeeping.

    Fails fast by default: the first unsolvable ``(environment,
    platform)`` pair raises :class:`SolveError` with the platform
    named, and no lockfile is written.  When *skip_unsolvable* is
    true, solver failures on an individual pair are reported via
    *on_skip* (if given) and the lockfile continues with the remaining
    pairs; :class:`AllTargetsUnsolvableError` is raised only if every
    pair fails.  Non-solver errors (missing channel, invalid manifest,
    etc.) always abort.

    Returns the path to the generated lockfile.
    """
    from conda.models.environment import Environment, EnvironmentConfig

    from .export import multiplatform_export

    host_platform = ctx.platform
    envs: list[Environment] = []
    failures: list[SolveError] = []

    for name, resolved in resolved_envs.items():
        # Intersect declared platforms with the requested subset.
        # ``requested=None`` means "lock everything the env declares";
        # an env with no declared platforms falls back to the host.
        declared = set(resolved.platforms or [host_platform])
        targets = sorted(declared if platforms is None else declared & set(platforms))
        if not targets:
            continue
        channels = tuple(str(ch) for ch in resolved.channels)
        for target in targets:
            if progress is not None:
                progress(name, target)
            try:
                records = resolved.solve_for_platform(
                    target, prefix=ctx.env_prefix(resolved.name)
                )
            except SolveError as exc:
                if not skip_unsolvable:
                    raise
                failures.append(exc)
                if on_skip is not None:
                    on_skip(name, target, exc)
                continue
            envs.append(
                Environment(
                    name=name,
                    platform=target,
                    config=EnvironmentConfig(channels=channels),
                    explicit_packages=records,
                )
            )

    if failures and not envs:
        raise AllTargetsUnsolvableError(failures)

    path = lockfile_path(ctx)
    path.write_text(multiplatform_export(envs), encoding="utf-8")
    return path


def install_from_lockfile(ctx: WorkspaceContext, env_name: str) -> None:
    """Install an environment from ``conda.lock``.

    Reads the lockfile via :class:`CondaLockLoader`, extracts the
    package list for *env_name* on the current platform, downloads the
    exact packages, and installs them into the environment prefix —
    bypassing the solver entirely.

    Raises ``LockfileNotFoundError`` if the lockfile is missing or does
    not contain the requested environment/platform.
    """
    from conda.misc import (
        get_package_records_from_explicit,
        install_explicit_packages,
    )

    path = lockfile_path(ctx)
    if not path.is_file():
        raise LockfileNotFoundError("(all)", path)

    loader = CondaLockLoader(path)
    try:
        env_data = loader._env_data(env_name)
    except (ValueError, OSError) as exc:
        raise LockfileNotFoundError(env_name, path) from exc

    platform_pkgs = env_data.get("packages", {}).get(ctx.platform)
    if platform_pkgs is None:
        raise LockfileNotFoundError(env_name, path)

    urls = [ref["conda"] for ref in platform_pkgs if "conda" in ref]

    prefix = ctx.env_prefix(env_name)
    prefix.mkdir(parents=True, exist_ok=True)

    records = get_package_records_from_explicit(urls)
    install_explicit_packages(
        package_cache_records=list(records),
        prefix=str(prefix),
    )
    sys.stdout.flush()
