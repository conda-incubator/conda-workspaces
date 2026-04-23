"""``conda workspace export`` — argparse shim over :mod:`conda_workspaces.export`.

Everything export-related — exporter dispatch, ``Environment``
builders for the three supported sources, and our own
``conda-workspaces-lock-v1`` serialisation — lives in
:mod:`conda_workspaces.export`.  This module only knows about the
CLI surface: it inspects ``args``, picks a source, picks an
exporter, and routes the result to stdout / JSON / a file.

See :mod:`conda_workspaces.export` for the programmatic API.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from conda.exceptions import CondaValueError
from rich.console import Console

from ...exceptions import EnvironmentNotFoundError
from ...export import resolve_exporter, run_exporter
from ...manifests.base import ManifestParser
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


def execute_export(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Build :class:`Environment` objects and hand them to the selected exporter."""
    if console is None:
        console = Console(highlight=False)

    config, ctx = workspace_context_from_args(args)
    env_name: str = args.environment or "default"

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if args.from_lockfile and args.from_prefix:
        raise CondaValueError(
            "--from-lockfile and --from-prefix are mutually exclusive."
        )

    requested_platforms: tuple[str, ...] = tuple(args.export_platforms or ())

    if args.from_lockfile:
        envs = ctx.envs_from_lockfile(
            env_name,
            requested_platforms=requested_platforms,
        )
    elif args.from_prefix:
        envs = ctx.envs_from_prefix(
            env_name,
            requested_platforms=requested_platforms,
            from_history=args.from_history,
            no_builds=args.no_builds,
            ignore_channels=args.ignore_channels,
        )
    else:
        envs = ctx.envs_from_manifest(
            env_name,
            requested_platforms=requested_platforms,
        )

    exporter, resolved_format = resolve_exporter(
        format_name=args.format,
        file_path=args.file,
    )

    if len(envs) > 1 and not exporter.multiplatform_export:
        raise CondaValueError(
            f"Multiple platforms are not supported for the '{exporter.name}' exporter."
        )

    content = run_exporter(exporter, envs)

    output_path: Path | None = args.file
    dry_run: bool = args.dry_run
    json_output: bool = args.json

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
    if output_path.is_file():
        # Give the parser a chance to merge the exported content into
        # an existing file rather than wholesale overwriting it.  The
        # default is still overwrite (same as ``conda export -f
        # environment.yaml``); :class:`PyprojectTomlParser` opts into a
        # nested-table merge so peer ``[project]`` / ``[build-system]``
        # tables survive.
        parser = ManifestParser.for_exporter_format(resolved_format)
        if parser is not None:
            content = parser.merge_export(output_path, content)
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
