"""``conda workspace info`` — show workspace or environment details."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from ...envs import get_environment_info
from ...resolver import known_platforms, resolve_all_environments, resolve_environment
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse

    from ...context import WorkspaceContext
    from ...models import WorkspaceConfig


def execute_info(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Show workspace overview or per-environment details."""
    config, ctx = workspace_context_from_args(args)

    if console is None:
        console = Console(highlight=False)

    env_name = getattr(args, "environment", None)
    json_output = getattr(args, "json", False)

    if env_name is None:
        return _show_workspace_info(config, ctx, console, json_output)
    return _show_env_info(config, ctx, env_name, console, json_output)


def _show_workspace_info(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    console: Console,
    json_output: bool,
) -> int:
    """Show workspace-level overview."""
    # Resolving is cheap (no solver, just feature merging) and lets us
    # surface the reachable platform set when features broaden it
    # beyond ``config.platforms``.
    resolved_envs = resolve_all_environments(config)
    known = sorted(known_platforms(config, resolved_envs.values()))

    info = {
        "manifest": config.manifest_path,
        "name": config.name or "(unnamed)",
        "version": config.version or "",
        "description": config.description or "",
        "channels": [ch.canonical_name for ch in config.channels],
        "platforms": config.platforms,
        "known_platforms": known,
        "environments": list(config.environments.keys()),
        "features": list(config.features.keys()),
    }

    if json_output:
        console.print_json(json.dumps(info))
    else:
        table = Table(show_header=False, show_edge=False, pad_edge=False)
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("Manifest", str(info["manifest"]))
        table.add_row("Name", info["name"])
        if info["version"]:
            table.add_row("Version", info["version"])
        if info["description"]:
            table.add_row("Description", info["description"])
        table.add_row("Channels", ", ".join(info["channels"]) or "(none)")
        table.add_row("Platforms", ", ".join(info["platforms"]) or "(all)")
        # Only surface the reachable set when a feature has broadened
        # it; otherwise the row is redundant with "Platforms".
        if set(known) != set(info["platforms"]):
            table.add_row("Known Platforms", ", ".join(known) or "(none)")
        table.add_row("Environments", ", ".join(info["environments"]))
        table.add_row("Features", ", ".join(info["features"]) or "(none)")
        console.print(table)

    return 0


def _show_env_info(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    env_name: str,
    console: Console,
    json_output: bool,
) -> int:
    """Show details for a single environment."""
    resolved = resolve_environment(config, env_name, ctx.platform)
    install_info = get_environment_info(ctx, env_name)

    info = {
        "name": env_name,
        "prefix": str(ctx.env_prefix(env_name)),
        "installed": install_info["exists"],
        "channels": [ch.canonical_name for ch in resolved.channels],
        "platforms": resolved.platforms,
        "channel_priority": resolved.channel_priority,
        "conda_dependencies": {
            name: dep.conda_build_form()
            for name, dep in resolved.conda_dependencies.items()
        },
        "pypi_dependencies": {
            name: str(dep) for name, dep in resolved.pypi_dependencies.items()
        },
    }

    if install_info["exists"]:
        info["packages_installed"] = install_info.get("packages", 0)

    if json_output:
        console.print_json(json.dumps(info))
    else:
        table = Table(show_header=False, show_edge=False, pad_edge=False)
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("Environment", info["name"])
        table.add_row("Prefix", info["prefix"])
        table.add_row("Installed", "yes" if info["installed"] else "no")
        if info["installed"]:
            table.add_row("Packages", str(info.get("packages_installed", "?")))
        table.add_row("Channels", ", ".join(info["channels"]) or "(none)")
        table.add_row("Platforms", ", ".join(info["platforms"]) or "(all)")
        if info["channel_priority"]:
            table.add_row("Channel priority", info["channel_priority"])
        console.print(table)

        if info["conda_dependencies"]:
            console.print("\n[bold]Conda dependencies:[/bold]")
            for _name, spec in sorted(info["conda_dependencies"].items()):
                console.print(f"  {spec}")

        if info["pypi_dependencies"]:
            console.print("\n[bold]PyPI dependencies:[/bold]")
            for _name, spec in sorted(info["pypi_dependencies"].items()):
                console.print(f"  {spec}")

    return 0
