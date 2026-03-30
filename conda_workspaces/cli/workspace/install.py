"""``conda workspace install`` — create or update workspace environments."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ...envs import install_environment
from ...exceptions import LockfileNotFoundError, LockfileStaleError
from ...lockfile import generate_lockfile, install_from_lockfile, lockfile_path
from ...resolver import resolve_all_environments, resolve_environment
from .. import status
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse

    from ...context import WorkspaceContext
    from ...models import WorkspaceConfig


def execute_install(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Install (create/update) workspace environments."""
    if console is None:
        console = Console(highlight=False)
    config, ctx = workspace_context_from_args(args)

    env_name = getattr(args, "environment", None)
    force = getattr(args, "force_reinstall", False)
    dry_run = getattr(args, "dry_run", False)
    locked = getattr(args, "locked", False)
    frozen = getattr(args, "frozen", False)

    if frozen:
        return _install_from_lockfile(ctx, config, env_name, console=console)

    if locked:
        _check_lockfile_freshness(ctx, config)
        return _install_from_lockfile(ctx, config, env_name, console=console)

    if env_name:
        resolved = resolve_environment(config, env_name, ctx.platform)
        install_environment(
            ctx, resolved, force_reinstall=force, dry_run=dry_run,
        )
        status.message(console, "Installed", "environment", env_name)
        if not dry_run:
            console.print()
            console.print(
                "[bold blue]Updating[/bold blue]"
                " [bold]conda.lock[/bold][dim]...[/dim]"
            )
            generate_lockfile(ctx, {env_name: resolved})
            console.print(
                "[bold cyan]Updated[/bold cyan]"
                " [bold]conda.lock[/bold]"
            )
    else:
        resolved_all = resolve_all_environments(config, ctx.platform)
        for i, (name, resolved) in enumerate(resolved_all.items()):
            if i > 0:
                console.print()
            status.message(
                console, "Installing", "environment", name,
                style="bold blue", ellipsis=True,
            )
            install_environment(
                ctx, resolved, force_reinstall=force, dry_run=dry_run,
            )
            status.message(console, "Installed", "environment", name)
        if not dry_run:
            console.print()
            console.print(
                "[bold blue]Updating[/bold blue]"
                " [bold]conda.lock[/bold][dim]...[/dim]"
            )
            generate_lockfile(ctx, resolved_all)
            console.print(
                "[bold cyan]Updated[/bold cyan]"
                " [bold]conda.lock[/bold]"
            )

    return 0


def _check_lockfile_freshness(ctx: WorkspaceContext, config: WorkspaceConfig) -> None:
    """Raise if the lockfile is missing or older than the manifest."""
    manifest = Path(config.manifest_path)
    lock = lockfile_path(ctx)

    if not lock.is_file():
        raise LockfileNotFoundError("(all)", lock)

    if manifest.stat().st_mtime > lock.stat().st_mtime:
        raise LockfileStaleError(manifest, lock)


def _install_from_lockfile(
    ctx: WorkspaceContext,
    config: WorkspaceConfig,
    env_name: str | None,
    *,
    console: Console,
) -> int:
    """Install environments from existing lockfiles (no solving)."""
    if env_name:
        install_from_lockfile(ctx, env_name)
        status.message(console, "Installed", "environment", env_name)
    else:
        env_names = list(config.environments)
        for i, name in enumerate(env_names):
            if i > 0:
                console.print()
            status.message(
                console, "Installing", "environment", name,
                style="bold blue", ellipsis=True,
            )
            install_from_lockfile(ctx, name)
            status.message(console, "Installed", "environment", name)

    return 0
