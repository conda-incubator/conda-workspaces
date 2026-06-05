"""``conda workspace install`` — create or update workspace environments."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ...exceptions import LockfileNotFoundError, LockfileStaleError
from ...lockfile import install_from_lockfile, lockfile_path, lockfile_status
from ...models import LockfileStatus
from .. import status
from . import workspace_context_from_args
from .sync import sync_environments

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
    no_lock = getattr(args, "no_lock", False)
    prefix = getattr(args, "prefix", None)
    target_prefix_override = getattr(args, "target_prefix_override", None)

    if frozen:
        return install_from_lockfile_all(
            ctx,
            config,
            env_name,
            console=console,
            prefix=prefix,
            target_prefix_override=target_prefix_override,
        )

    strict = locked or (ctx.is_ci and not no_lock)
    if strict:
        lock = lockfile_status(ctx, config)
        if lock.status == LockfileStatus.MISSING:
            raise LockfileNotFoundError("(all)", lockfile_path(ctx))
        if lock.status == LockfileStatus.OUT_OF_DATE:
            raise LockfileStaleError(
                Path(config.manifest_path),
                lockfile_path(ctx),
                reason=lock.reason,
            )
        return install_from_lockfile_all(
            ctx,
            config,
            env_name,
            console=console,
            prefix=prefix,
            target_prefix_override=target_prefix_override,
        )

    if not no_lock and not force:
        lock = lockfile_status(ctx, config)
        if lock.status == LockfileStatus.UP_TO_DATE:
            return install_from_lockfile_all(
                ctx,
                config,
                env_name,
                console=console,
                prefix=prefix,
                target_prefix_override=target_prefix_override,
            )
        if lock.status == LockfileStatus.OUT_OF_DATE:
            console.print(
                f"[bold yellow]Lockfile out of date[/bold yellow]:"
                f" {lock.reason}. Re-solving environments."
            )

    env_names = [env_name] if env_name else list(config.environments.keys())
    sync_environments(
        config,
        ctx,
        env_names,
        force_reinstall=force,
        dry_run=dry_run,
        console=console,
    )
    return 0


def install_from_lockfile_all(
    ctx: WorkspaceContext,
    config: WorkspaceConfig,
    env_name: str | None,
    *,
    console: Console,
    prefix: Path | None = None,
    target_prefix_override: Path | None = None,
) -> int:
    """Install environments from existing lockfiles (no solving)."""
    if (prefix is not None or target_prefix_override is not None) and not env_name:
        from ...exceptions import CondaWorkspacesError

        raise CondaWorkspacesError(
            "Explicit prefix installation requires an environment name.",
            hints=["Pass -e/--environment with --prefix."],
        )

    if env_name:
        status.message(
            console,
            "Installing",
            "environment",
            env_name,
            style="bold blue",
            ellipsis=True,
        )
        if prefix is None and target_prefix_override is None:
            install_from_lockfile(ctx, env_name)
        else:
            install_from_lockfile(
                ctx,
                env_name,
                prefix=prefix,
                target_prefix_override=target_prefix_override,
            )
        status.message(console, "Installed", "environment", env_name)
    else:
        env_names = list(config.environments)
        for i, name in enumerate(env_names):
            if i > 0:
                console.print()
            status.message(
                console,
                "Installing",
                "environment",
                name,
                style="bold blue",
                ellipsis=True,
            )
            install_from_lockfile(ctx, name)
            status.message(console, "Installed", "environment", name)

    return 0
