"""``conda workspace list`` — list packages or environments."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from conda.core.envs_manager import PrefixData
from rich.console import Console
from rich.table import Table

from ...envs import list_installed_environments
from ...exceptions import EnvironmentNotFoundError, EnvironmentNotInstalledError
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse

    from ...context import WorkspaceContext
    from ...models import WorkspaceConfig


def execute_list(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """List packages in an environment, or environments in the workspace."""
    config, ctx = workspace_context_from_args(args)

    if console is None:
        console = Console()

    json_output = getattr(args, "json", False)

    if getattr(args, "envs", False):
        installed_only = getattr(args, "installed", False)
        return _list_environments(config, ctx, console, json_output, installed_only)

    env_name = getattr(args, "environment", "default")
    return _list_packages(config, ctx, env_name, console, json_output)


def _list_packages(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    env_name: str,
    console: Console,
    json_output: bool,
) -> int:
    """List installed packages in an environment."""
    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)

    prefix = ctx.env_prefix(env_name)
    pd = PrefixData(str(prefix))
    records = sorted(pd.iter_records(), key=lambda r: r.name)

    if json_output:
        console.print_json(
            json.dumps(
                [
                    {"name": r.name, "version": r.version, "build": r.build}
                    for r in records
                ]
            )
        )
    else:
        if not records:
            console.print(
                f"No packages in [bold]{env_name}[/bold] environment."
                f" Run 'conda workspace install -n {env_name}' first."
            )
            return 0

        table = Table(show_edge=False, pad_edge=False)
        table.add_column("Name")
        table.add_column("Version")
        table.add_column("Build")
        for r in records:
            table.add_row(r.name, r.version, r.build)
        console.print(table)

    return 0


def _list_environments(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    console: Console,
    json_output: bool,
    installed_only: bool,
) -> int:
    """List environments defined in the workspace."""
    installed = set(list_installed_environments(ctx))

    rows: list[dict[str, str | bool | list[str]]] = []
    for name, env in sorted(config.environments.items()):
        if installed_only and name not in installed:
            continue
        rows.append(
            {
                "name": name,
                "features": env.features,
                "installed": name in installed,
            }
        )

    if json_output:
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print(
                "No environments defined."
                " Run 'conda workspace init' to create a workspace."
            )
            return 0

        table = Table(show_edge=False, pad_edge=False)
        table.add_column("Name")
        table.add_column("Features")
        table.add_column("Installed")
        for row in rows:
            feats = ", ".join(row["features"]) if row["features"] else "(default)"  # type: ignore[arg-type]
            status = "yes" if row["installed"] else "no"
            table.add_row(row["name"], feats, status)  # type: ignore[arg-type]
        console.print(table)

    return 0
