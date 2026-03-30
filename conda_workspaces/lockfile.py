"""Lockfile generation and consumption for reproducible environments.

Produces a single ``conda.lock`` at the workspace root.  The file
captures all environments and platforms so that installations can be
reproduced exactly without running the solver.

The format is a YAML document with three top-level keys::

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

On the *write* side, ``generate_lockfile`` solves each environment
and records the result.  On the *read* side, ``install_from_lockfile`` extracts
the package list for one environment + platform and installs the exact
URLs, bypassing the solver entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conda.common.serialize.yaml import dump as yaml_dump
from conda.common.serialize.yaml import load as yaml_load
from conda_lockfiles.rattler_lock.v6 import _record_to_dict

from .exceptions import LockfileNotFoundError, SolveError

if TYPE_CHECKING:
    from typing import Any

    from .context import WorkspaceContext
    from .resolver import ResolvedEnvironment

#: Lockfile format version.
LOCKFILE_VERSION = 1

#: The canonical lockfile filename.
LOCKFILE_NAME = "conda.lock"


def lockfile_path(ctx: WorkspaceContext) -> Path:
    """Return the path to the workspace lockfile (``<root>/conda.lock``)."""
    return ctx.root / LOCKFILE_NAME


def _build_lockfile_dict(
    environments: dict[tuple[str, str], list],
    channels_by_env: dict[str, list[str]],
) -> dict[str, Any]:
    """Build the lockfile dict from solved package records.

    *environments* maps ``(env_name, platform)`` pairs to lists of
    :class:`~conda.models.records.PackageRecord` objects.

    *channels_by_env* maps environment names to ordered channel URLs.
    """
    seen_urls: set[str] = set()
    packages: list[dict[str, Any]] = []
    envs_dict: dict[str, dict[str, Any]] = {}

    for (env_name, platform), records in sorted(environments.items()):
        if env_name not in envs_dict:
            channels = channels_by_env.get(env_name, [])
            envs_dict[env_name] = {
                "channels": [{"url": ch} for ch in channels],
                "packages": {},
            }

        platform_refs: list[dict[str, str]] = []
        for pkg in sorted(records, key=lambda p: p.name):
            platform_refs.append({"conda": pkg.url})
            if pkg.url not in seen_urls:
                packages.append(_record_to_dict(pkg))
                seen_urls.add(pkg.url)

        envs_dict[env_name]["packages"][platform] = platform_refs

    return {
        "version": LOCKFILE_VERSION,
        "environments": envs_dict,
        "packages": packages,
    }


def _solve_for_records(
    ctx: WorkspaceContext,
    resolved: ResolvedEnvironment,
) -> list:
    """Solve an environment and return the resulting package records.

    Uses conda's solver API to resolve dependencies without installing,
    producing the list of exact packages that would be installed.
    Applies the same transformations as ``install_environment``:
    PyPI deps are translated and merged, system requirements are added
    as virtual package constraints, and channel priority is honoured.
    """
    from conda.base.context import context as conda_context
    from conda.exceptions import UnsatisfiableError
    from conda.models.match_spec import MatchSpec

    from .envs import (
        _apply_system_requirements,
        _build_pypi_specs,
        _channel_priority_override,
    )

    specs = [
        MatchSpec(dep.conda_build_form())
        for dep in resolved.conda_dependencies.values()
    ]

    specs.extend(_build_pypi_specs(resolved))
    _apply_system_requirements(resolved, specs)

    if not specs:
        return []

    solver_backend = conda_context.plugin_manager.get_cached_solver_backend()
    if solver_backend is None:
        raise SolveError(resolved.name, "No solver backend found")

    prefix = str(ctx.env_prefix(resolved.name))

    with _channel_priority_override(resolved.channel_priority):
        solver = solver_backend(
            prefix,
            list(resolved.channels),
            conda_context.subdirs,
            specs_to_add=specs,
        )

        try:
            return list(solver.solve_final_state())
        except (UnsatisfiableError, SystemExit) as exc:
            raise SolveError(resolved.name, str(exc)) from exc


def generate_lockfile(
    ctx: WorkspaceContext,
    resolved_envs: dict[str, ResolvedEnvironment],
) -> Path:
    """Generate a ``conda.lock`` by solving workspace environments.

    Solves each environment in *resolved_envs* and writes the results
    to a single ``conda.lock`` YAML file at the workspace root.
    Solver output is suppressed to avoid noise since the caller
    provides its own status messages.

    Returns the path to the generated lockfile.
    """
    import os
    import sys

    from conda.base.context import context as conda_context

    platform = ctx.platform
    environments: dict[tuple[str, str], list] = {}
    channels_by_env: dict[str, list[str]] = {}

    with conda_context._override("quiet", True):
        real_stdout = sys.stdout
        devnull = open(os.devnull, "w")
        try:
            sys.stdout = devnull
            for name, resolved in resolved_envs.items():
                records = _solve_for_records(ctx, resolved)
                environments[(name, platform)] = records
                channels_by_env[name] = [str(ch) for ch in resolved.channels]
        finally:
            sys.stdout = real_stdout
            devnull.close()

    data = _build_lockfile_dict(environments, channels_by_env)

    path = lockfile_path(ctx)
    with path.open("w", encoding="utf-8") as fh:
        yaml_dump(data, fh)
    return path


def _read_lockfile_data(ctx: WorkspaceContext) -> dict[str, Any]:
    """Parse ``conda.lock`` and return the raw dict."""
    path = lockfile_path(ctx)
    if not path.is_file():
        raise LockfileNotFoundError("(all)", path)
    with path.open(encoding="utf-8") as fh:
        return yaml_load(fh)


def _extract_env_packages(
    data: dict[str, Any],
    env_name: str,
    platform: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(url, metadata)`` pairs for an env+platform from lockfile data.

    Raises ``LockfileNotFoundError`` if the environment or platform is
    missing.
    """
    environments = data.get("environments", {})
    if env_name not in environments:
        raise LockfileNotFoundError(
            env_name,
            Path(LOCKFILE_NAME),
        )

    env_data = environments[env_name]
    platform_pkgs = env_data.get("packages", {}).get(platform)
    if platform_pkgs is None:
        raise LockfileNotFoundError(
            env_name,
            Path(LOCKFILE_NAME),
        )

    # Build a lookup from the top-level packages list
    all_packages = data.get("packages", [])
    lookup: dict[str, dict[str, Any]] = {}
    for pkg in all_packages:
        url = pkg.get("conda")
        if url:
            lookup[url] = pkg

    # Match platform references against the packages list
    result: list[tuple[str, dict[str, Any]]] = []
    for ref in platform_pkgs:
        url = ref.get("conda", "")
        metadata = lookup.get(url, {})
        result.append((url, metadata))

    return result


def install_from_lockfile(ctx: WorkspaceContext, env_name: str) -> None:
    """Install an environment from ``conda.lock``.

    Reads the lockfile, extracts the package list for *env_name* on
    the current platform, downloads the exact packages, and installs
    them into the environment prefix — bypassing the solver entirely.

    Raises ``LockfileNotFoundError`` if the lockfile is missing or
    does not contain the requested environment/platform.
    """
    from conda.misc import (
        get_package_records_from_explicit,
        install_explicit_packages,
    )

    data = _read_lockfile_data(ctx)
    pkg_pairs = _extract_env_packages(data, env_name, ctx.platform)

    urls = [url for url, _meta in pkg_pairs]

    prefix = ctx.env_prefix(env_name)
    prefix.mkdir(parents=True, exist_ok=True)

    import sys

    records = get_package_records_from_explicit(urls)
    install_explicit_packages(
        package_cache_records=list(records),
        prefix=str(prefix),
    )
    sys.stdout.flush()
