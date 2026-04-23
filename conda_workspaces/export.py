"""Export: workspace manifests and environments to conda exporter plugins.

This module is ``conda_workspaces``' home for everything export-side.
Two responsibilities sit side by side:

* **Provider** — the ``conda-workspaces-lock-v1`` exporter we register
  with conda (see :mod:`conda_workspaces.plugin`).
  :func:`multiplatform_export` is the callable conda invokes for
  ``conda export --format=conda-workspaces-lock-v1``; it reuses the
  same serialisation logic ``conda workspace lock`` uses so format
  output is consistent across entry points.

* **Consumer** — :func:`resolve_exporter` / :func:`run_exporter` look
  up and invoke *any* exporter registered on
  ``conda_context.plugin_manager`` (built-in ``environment-yaml`` /
  ``environment-json``, our own ``conda-workspaces-lock-v1``, any
  third-party plugin).  The three ``envs_from_*`` builders turn the
  three supported workspace sources — manifest, installed prefix,
  existing lockfile — into the
  :class:`~conda.models.environment.Environment` objects an exporter
  consumes.  Public methods on :class:`WorkspaceContext`
  (:meth:`WorkspaceContext.envs_from_manifest`, etc.) delegate here;
  this keeps the heavy conda imports out of ``context.py``.

The CLI handler at :mod:`conda_workspaces.cli.workspace.export` is a
thin argparse shim over these helpers; third-party code that wants
the same capabilities without the CLI should go through
:class:`WorkspaceContext` (preferred) or import the builders
directly.

Each builder reuses an existing primitive where one exists:

* :func:`envs_from_prefix` — same :meth:`Environment.from_prefix` /
  :meth:`Environment.extrapolate` pair that
  :func:`conda.cli.main_export.execute` uses.
* :func:`envs_from_lockfile` — our own
  :class:`~conda_workspaces.lockfile.CondaLockLoader`
  ``EnvironmentSpecBase`` plugin (``env_for``), identical to the path
  conda takes when it reads ``--file conda.lock`` through
  :meth:`Environment.from_cli_with_file_envs`.
* :func:`envs_from_manifest` — the only source without a conda
  equivalent.  Turning a declared-but-unsolved ``conda.toml``
  manifest into an :class:`Environment` is the capability that
  distinguishes ``conda workspace export`` from ``conda export``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.common.serialize.yaml import dump as yaml_dump
from conda.exceptions import CondaValueError, EnvironmentExporterNotDetected
from conda.models.environment import Environment
from conda.models.environment import EnvironmentConfig as CondaEnvConfig
from conda.models.match_spec import MatchSpec

from .exceptions import (
    EnvironmentNotInstalledError,
    LockfileNotFoundError,
    PlatformError,
)
from .lockfile import CondaLockLoader, lockfile_path
from .resolver import resolve_environment

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from conda.plugins.types import CondaEnvironmentExporter

    from .context import WorkspaceContext


DEFAULT_FORMAT = "environment-yaml"

# Canonical key the built-in environment-yaml / environment-json
# exporters use to roundtrip PyPI deps through ``external_packages``.
# Older conda releases ship the value without exposing a constant, so
# we hard-code it here to stay compatible with conda >= 26.1.
_EXTERNAL_PACKAGES_PYPI_KEY = "pip"


def multiplatform_export(envs: Iterable[Environment]) -> str:
    """Export ``Environment`` objects to a ``conda.lock`` YAML string.

    Registered as the ``multiplatform_export`` callable on our
    :class:`~conda.plugins.types.CondaEnvironmentExporter`; conda
    calls it with one :class:`Environment` per platform.  Composition
    runs through :meth:`CondaLockLoader.compose` so callers that want
    the pre-YAML dict representation share the exact same logic.
    """
    env_dict = CondaLockLoader.compose(envs)
    buf = io.StringIO()
    yaml_dump(env_dict, buf)
    return buf.getvalue()


def envs_from_manifest(
    ctx: WorkspaceContext,
    env_name: str,
    *,
    requested_platforms: tuple[str, ...] = (),
) -> list[Environment]:
    """Implementation backing :meth:`WorkspaceContext.envs_from_manifest`.

    Kept at module level so the heavy conda imports
    (``conda.models.environment``, ``conda.models.match_spec``) load
    only when the consumer actually asks for an export, not every
    time :class:`WorkspaceContext` is instantiated.
    """
    config = ctx.config
    try:
        declared = resolve_environment(config, env_name)
    except PlatformError:
        # Manifest declares platforms conda doesn't know; fall back to
        # the host platform so the export still produces something
        # useful rather than crashing on validation alone.
        targets: tuple[str, ...] = (ctx.platform,)
    else:
        targets = declared.target_platforms(
            requested=requested_platforms,
            fallback=ctx.platform,
        )

    envs: list[Environment] = []
    for platform in targets:
        resolved = resolve_environment(config, env_name, platform)

        requested_packages = [
            MatchSpec(dep.conda_build_form())
            for dep in resolved.conda_dependencies.values()
        ]

        external_packages: dict[str, list[str]] = {}
        pypi_entries = [
            str(dep).strip()
            for dep in resolved.pypi_dependencies.values()
            if not dep.path and not dep.git and not dep.url
        ]
        if pypi_entries:
            external_packages[_EXTERNAL_PACKAGES_PYPI_KEY] = pypi_entries

        envs.append(
            Environment(
                name=env_name,
                platform=platform,
                config=CondaEnvConfig(
                    channels=tuple(ch.canonical_name for ch in resolved.channels),
                ),
                requested_packages=requested_packages,
                external_packages=external_packages,
            )
        )

    return envs


def envs_from_prefix(
    ctx: WorkspaceContext,
    env_name: str,
    *,
    requested_platforms: tuple[str, ...] = (),
    from_history: bool = False,
    no_builds: bool = False,
    ignore_channels: bool = False,
) -> list[Environment]:
    """Implementation backing :meth:`WorkspaceContext.envs_from_prefix`."""
    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)
    prefix_env = Environment.from_prefix(
        prefix=str(ctx.env_prefix(env_name)),
        name=env_name,
        platform=ctx.platform,
        from_history=from_history,
        no_builds=no_builds,
        ignore_channels=ignore_channels,
        channels=list(conda_context.channels),
    )
    if not requested_platforms or requested_platforms == (ctx.platform,):
        return [prefix_env]
    return [prefix_env.extrapolate(p) for p in requested_platforms]


def envs_from_lockfile(
    ctx: WorkspaceContext,
    env_name: str,
    *,
    requested_platforms: tuple[str, ...] = (),
) -> list[Environment]:
    """Implementation backing :meth:`WorkspaceContext.envs_from_lockfile`."""
    path = lockfile_path(ctx)
    if not path.is_file():
        raise LockfileNotFoundError(env_name, path)
    loader = CondaLockLoader(path)
    available = loader.available_platforms
    if requested_platforms:
        unknown = [p for p in requested_platforms if p not in available]
        if unknown:
            raise PlatformError(unknown[0], list(available))
        requested_set = set(requested_platforms)
        targets = tuple(p for p in available if p in requested_set)
    else:
        targets = available
    if not targets:
        raise LockfileNotFoundError(env_name, path)
    try:
        return [loader.env_for(platform=p, name=env_name) for p in targets]
    except ValueError as exc:
        raise LockfileNotFoundError(env_name, path) from exc


def resolve_exporter(
    *,
    format_name: str | None,
    file_path: Path | None,
) -> tuple[CondaEnvironmentExporter, str]:
    """Look up the plugin exporter to use and return ``(exporter, name)``.

    Thin public wrapper over :attr:`conda.base.context.context.plugin_manager`
    — the consumer-side counterpart to :func:`multiplatform_export`
    on the provider side.  Kept as a free function because no existing
    workspace class owns exporter dispatch and conda's
    ``CondaEnvironmentExporter`` is not ours to extend.

    Precedence matches ``conda export``:

    1. Explicit ``--format`` wins and is looked up by name or alias.
    2. Otherwise, if ``--file`` is given, detect by filename pattern.
    3. Otherwise, default to :data:`DEFAULT_FORMAT`.
    """
    pm = conda_context.plugin_manager

    if format_name:
        exporter = pm.get_environment_exporter_by_format(format_name)
    elif file_path is not None:
        try:
            exporter = pm.detect_environment_exporter(str(file_path))
        except EnvironmentExporterNotDetected:
            exporter = pm.get_environment_exporter_by_format(DEFAULT_FORMAT)
    else:
        exporter = pm.get_environment_exporter_by_format(DEFAULT_FORMAT)
    return exporter, exporter.name


def run_exporter(
    exporter: CondaEnvironmentExporter,
    envs: list[Environment],
) -> str:
    """Invoke *exporter*, preferring ``multiplatform_export`` when available.

    Companion to :func:`resolve_exporter`: once an exporter has been
    selected, this normalises the conda plugin interface's two entry
    points (``multiplatform_export`` for a list of environments,
    ``export`` for a single one) and always appends a trailing newline
    so callers can write the result verbatim.
    """
    if exporter.multiplatform_export is not None:
        content = exporter.multiplatform_export(envs)
    elif exporter.export is not None:
        content = exporter.export(envs[0])
    else:
        raise CondaValueError(
            f"Exporter '{exporter.name}' has no registered export method."
        )
    return content.rstrip() + "\n"
