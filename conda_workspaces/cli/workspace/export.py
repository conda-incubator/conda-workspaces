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
from ...export import (
    build_from_declared,
    build_from_lockfile,
    build_from_prefix,
    resolve_exporter,
    run_exporter,
)
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
        envs = build_from_lockfile(
            ctx=ctx,
            env_name=env_name,
            requested_platforms=requested_platforms,
        )
    elif from_prefix:
        envs = build_from_prefix(
            ctx=ctx,
            env_name=env_name,
            requested_platforms=requested_platforms,
            from_history=bool(getattr(args, "from_history", False)),
            no_builds=bool(getattr(args, "no_builds", False)),
            ignore_channels=bool(getattr(args, "ignore_channels", False)),
        )
    else:
        envs = build_from_declared(
            config=config,
            ctx=ctx,
            env_name=env_name,
            requested_platforms=requested_platforms,
        )

    exporter, resolved_format = resolve_exporter(
        format_name=getattr(args, "format", None),
        file_path=getattr(args, "file", None),
    )

    if len(envs) > 1 and not exporter.multiplatform_export:
        raise CondaValueError(
            f"Multiple platforms are not supported for the '{exporter.name}' exporter."
        )

    content = run_exporter(exporter, envs)

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
