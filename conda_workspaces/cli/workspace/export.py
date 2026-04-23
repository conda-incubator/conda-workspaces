"""``conda workspace export`` — via the conda environment exporter plugin.

This command is a thin consumer of conda's built-in environment
exporter registry (``context.plugin_manager``).  It builds one or
more :class:`conda.models.environment.Environment` objects, then
delegates serialisation to whichever ``CondaEnvironmentExporter``
the user selected via ``--format`` or ``--file`` — default
``environment-yaml`` / ``environment-json`` exporters live in
:mod:`conda.plugins.environment_exporters`, our own
``conda-workspaces-lock-v1`` in :mod:`conda_workspaces.plugin`, and
any third-party exporter plugged into conda is picked up for free.

Each of the three ``Environment`` sources reuses an existing primitive:

* ``--from-prefix`` — the same :meth:`Environment.from_prefix` +
  :meth:`Environment.extrapolate` pair that
  :func:`conda.cli.main_export.execute` uses.
* ``--from-lockfile`` — our own
  :class:`~conda_workspaces.lockfile.CondaLockLoader` env-spec plugin
  (``env_for``), identical to the path conda takes when it reads
  ``--file conda.lock`` through
  :meth:`Environment.from_cli_with_file_envs`.
* (default) ``declared`` — the only source without a conda equivalent,
  exposed as the public :func:`build_from_declared`.  This is the
  capability that distinguishes ``conda workspace export`` from
  ``conda export``: emit an ``environment.yml`` / ``conda.lock`` /
  anything directly from the manifest without installing or solving
  first, and the hook point for plugin exporters that want to consume
  declared-but-unsolved workspace environments directly.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.exceptions import CondaValueError, EnvironmentExporterNotDetected
from conda.models.environment import Environment
from conda.models.environment import EnvironmentConfig as CondaEnvConfig
from conda.models.match_spec import MatchSpec
from rich.console import Console

from ...exceptions import (
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
    LockfileNotFoundError,
    PlatformError,
)
from ...lockfile import CondaLockLoader, lockfile_path
from ...resolver import resolve_environment
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from conda.plugins.types import CondaEnvironmentExporter

    from ...context import WorkspaceContext
    from ...models import WorkspaceConfig


DEFAULT_FORMAT = "environment-yaml"

# Canonical key the built-in environment-yaml / environment-json
# exporters use to roundtrip PyPI deps through ``external_packages``.
# Older conda releases ship the value without exposing a constant, so
# we hard-code it here to stay compatible with conda >= 26.1.
_EXTERNAL_PACKAGES_PYPI_KEY = "pip"


def execute_export(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Build :class:`Environment` objects and hand them to the selected exporter."""
    if console is None:
        console = Console(highlight=False)

    config, ctx = workspace_context_from_args(args)
    env_name: str = getattr(args, "environment", None) or "default"

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    from_lockfile = bool(getattr(args, "from_lockfile", False))
    from_prefix = bool(getattr(args, "from_prefix", False))
    if from_lockfile and from_prefix:
        raise CondaValueError(
            "--from-lockfile and --from-prefix are mutually exclusive."
        )

    requested_platforms: tuple[str, ...] = tuple(
        getattr(args, "export_platforms", None) or ()
    )
    if from_lockfile:
        # ``CondaLockLoader`` is this package's ``EnvironmentSpecBase``
        # plugin; ``env_for`` is the same entry point
        # ``Environment.from_cli_with_file_envs`` uses when conda reads
        # ``--file conda.lock``, so we get identical ``Environment``
        # objects without rebuilding the reader.
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
            envs = [loader.env_for(platform=p, name=env_name) for p in targets]
        except ValueError as exc:
            raise LockfileNotFoundError(env_name, path) from exc
    elif from_prefix:
        # Same primitives ``conda.cli.main_export.execute`` uses:
        # ``Environment.from_prefix`` for the host platform and
        # ``Environment.extrapolate`` to project it onto cross-platform
        # targets.  The extra work here is only the workspace-prefix
        # lookup + ``EnvironmentNotInstalledError`` guard.
        if not ctx.env_exists(env_name):
            raise EnvironmentNotInstalledError(env_name)
        prefix_env = Environment.from_prefix(
            prefix=str(ctx.env_prefix(env_name)),
            name=env_name,
            platform=ctx.platform,
            from_history=bool(getattr(args, "from_history", False)),
            no_builds=bool(getattr(args, "no_builds", False)),
            ignore_channels=bool(getattr(args, "ignore_channels", False)),
            channels=list(conda_context.channels),
        )
        if not requested_platforms or requested_platforms == (ctx.platform,):
            envs = [prefix_env]
        else:
            envs = [prefix_env.extrapolate(p) for p in requested_platforms]
    else:
        envs = build_from_declared(
            config=config,
            ctx=ctx,
            env_name=env_name,
            requested_platforms=requested_platforms,
        )

    exporter, resolved_format = _resolve_exporter(
        format_name=getattr(args, "format", None),
        file_path=getattr(args, "file", None),
    )

    if len(envs) > 1 and not exporter.multiplatform_export:
        raise CondaValueError(
            f"Multiple platforms are not supported for the '{exporter.name}' exporter."
        )

    content = _run_exporter(exporter, envs)

    output_path: Path | None = getattr(args, "file", None)
    dry_run = bool(getattr(args, "dry_run", False))
    json_output = bool(getattr(args, "json", False))

    if dry_run or output_path is None:
        # Always mirror to stdout when there's no file (or the user
        # asked to preview the write without touching disk).
        if not json_output:
            sys.stdout.write(content)
            sys.stdout.flush()
        else:
            console.print_json(
                json.dumps(
                    {
                        "success": True,
                        "format": resolved_format,
                        "environment": env_name,
                        "content": content,
                    }
                )
            )
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    if json_output:
        console.print_json(
            json.dumps(
                {
                    "success": True,
                    "file": str(output_path),
                    "format": resolved_format,
                    "environment": env_name,
                }
            )
        )
    else:
        console.print(
            f"[bold green]Exported[/bold green] environment "
            f"[bold]{env_name}[/bold] to [bold]{output_path}[/bold]"
            f" ([dim]{resolved_format}[/dim])"
        )

    return 0


def build_from_declared(
    *,
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    env_name: str,
    requested_platforms: tuple[str, ...],
) -> list[Environment]:
    """Resolve declared specs from the manifest into one ``Environment`` per platform.

    Produces an :class:`Environment` with ``requested_packages`` only
    (no solver, no installed packages required) — the novel piece of
    ``conda workspace export``.  conda itself has no primitive for
    "build an :class:`Environment` from a manifest without installing
    it", which is why this lives here rather than in upstream conda.

    Public because it is the distinguishing capability of
    ``conda workspace export`` and the natural entry point for
    third-party exporter plugins (or other tooling) that want to turn
    a ``conda.toml`` manifest into a list of :class:`Environment`
    objects without going through the CLI.
    """
    try:
        declared = resolve_environment(config, env_name)
        declared_platforms = (
            tuple(declared.platforms) or tuple(config.platforms) or (ctx.platform,)
        )
    except PlatformError:
        declared_platforms = (ctx.platform,)

    targets = _targets_for_env(
        declared=declared_platforms,
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


def _targets_for_env(
    *,
    declared: tuple[str, ...],
    requested: tuple[str, ...],
    fallback: str,
) -> tuple[str, ...]:
    """Intersect *declared* platforms with any explicitly *requested* ones."""
    declared_set = set(declared) or {fallback}
    if not requested:
        return tuple(sorted(declared_set))
    unknown = [p for p in requested if p not in declared_set]
    if unknown:
        raise PlatformError(unknown[0], sorted(declared_set))
    return tuple(p for p in requested if p in declared_set)


def _resolve_exporter(
    *,
    format_name: str | None,
    file_path: Path | None,
) -> tuple[CondaEnvironmentExporter, str]:
    """Look up the plugin exporter to use and return ``(exporter, name)``.

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


def _run_exporter(
    exporter: CondaEnvironmentExporter,
    envs: list[Environment],
) -> str:
    """Invoke the exporter, preferring ``multiplatform_export`` when available."""
    if exporter.multiplatform_export is not None:
        content = exporter.multiplatform_export(envs)
    elif exporter.export is not None:
        content = exporter.export(envs[0])
    else:
        raise CondaValueError(
            f"Exporter '{exporter.name}' has no registered export method."
        )
    return content.rstrip() + "\n"
