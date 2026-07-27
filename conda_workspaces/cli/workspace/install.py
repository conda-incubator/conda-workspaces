"""``conda workspace install`` — create or update workspace environments."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ...context import isolated_package_cache
from ...exceptions import (
    CondaWorkspacesError,
    LockfileNotFoundError,
    LockfileStaleError,
)
from ...lockfile import (
    MAX_LOCKFILE_BYTES,
    LockfileInstallPlan,
    check_lockfile_satisfiability,
    load_lockfile_data,
    lockfile_path,
    lockfile_status,
)
from ...models import LockfileStatus
from ...paths import read_regular_file_bytes
from ...publication import WorkspacePublication
from .. import status
from . import workspace_context_from_args
from .sync import sync_environments

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    from ...context import WorkspaceContext
    from ...models import WorkspaceConfig


def execute_install(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Install (create/update) workspace environments."""
    if console is None:
        console = Console(highlight=False)
    requested_manifest_path = getattr(args, "manifest_file", None)
    if requested_manifest_path is not None:
        WorkspacePublication.validate_manifest_path(Path(requested_manifest_path))
    config, ctx = workspace_context_from_args(args, for_mutation=True)

    env_name = getattr(args, "environment", None)
    force = getattr(args, "force_reinstall", False)
    dry_run = getattr(args, "dry_run", False)
    locked = getattr(args, "locked", False)
    frozen = getattr(args, "frozen", False)
    no_lock = getattr(args, "no_lock", False)
    prefix = getattr(args, "prefix", None)
    target_prefix_override = getattr(args, "target_prefix_override", None)
    WorkspacePublication.validate_manifest_path(Path(config.manifest_path))
    publication = (
        None if dry_run else WorkspacePublication.from_current_manifest(ctx, "install")
    )
    validate_workspace = (
        publication.validate_manifest_generation if publication is not None else None
    )
    read_lockfile = publication.read_lockfile_bytes if publication is not None else None

    use_lockfile = frozen
    if not frozen:
        strict = locked or (ctx.is_ci and not no_lock)
        if strict or not no_lock:
            lock = lockfile_status(ctx, config)
            if strict:
                if lock.status == LockfileStatus.MISSING:
                    raise LockfileNotFoundError("(all)", lockfile_path(ctx))
                if lock.status == LockfileStatus.OUT_OF_DATE:
                    raise LockfileStaleError(
                        Path(config.manifest_path),
                        lockfile_path(ctx),
                        reason=lock.reason,
                    )
                use_lockfile = True
            elif lock.status == LockfileStatus.UP_TO_DATE:
                use_lockfile = True
            elif lock.status == LockfileStatus.OUT_OF_DATE:
                console.print(
                    f"[bold yellow]Lockfile out of date[/bold yellow]:"
                    f" {status.escape_for_console(lock.reason)}."
                    " Re-solving environments."
                )

    publication_guard = (
        publication.guard() if publication is not None else nullcontext()
    )
    with publication_guard:
        if use_lockfile:
            return install_from_lockfile_all(
                ctx,
                config,
                env_name,
                console=console,
                prefix=prefix,
                target_prefix_override=target_prefix_override,
                dry_run=dry_run,
                force_reinstall=force,
                validate_current=not frozen,
                validate_workspace=validate_workspace,
                read_lockfile=read_lockfile,
            )

        env_names = [env_name] if env_name else list(config.environments.keys())
        sync_environments(
            config,
            ctx,
            env_names,
            force_reinstall=force,
            dry_run=dry_run,
            publish_lockfile=(
                publication.publish_lockfile if publication is not None else None
            ),
            validate_workspace=validate_workspace,
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
    target_prefix_override: str | Path | None = None,
    dry_run: bool = False,
    force_reinstall: bool = False,
    validate_current: bool = False,
    validate_workspace: Callable[[], None] | None = None,
    read_lockfile: Callable[[], bytes] | None = None,
) -> int:
    """Install environments from existing lockfiles (no solving)."""
    if (prefix is not None or target_prefix_override is not None) and not env_name:
        raise CondaWorkspacesError(
            "Explicit prefix installation requires an environment name.",
            hints=["Pass -e/--environment with --prefix."],
        )
    if force_reinstall and prefix is not None:
        raise CondaWorkspacesError(
            "Force reinstall cannot be combined with an explicit prefix."
        )

    env_names = [env_name] if env_name else list(config.environments)
    path = lockfile_path(ctx)
    try:
        if validate_workspace is not None:
            validate_workspace()
        lockfile_data = load_lockfile_data(
            read_lockfile()
            if read_lockfile is not None
            else read_regular_file_bytes(
                path,
                maximum_bytes=MAX_LOCKFILE_BYTES,
                label="workspace lockfile",
            )
        )
        if validate_workspace is not None:
            validate_workspace()
    except (OSError, ValueError) as exc:
        raise LockfileNotFoundError("(all)", path) from exc
    if validate_current:
        current = check_lockfile_satisfiability(config, lockfile_data, ctx.platform)
        if current.status != LockfileStatus.UP_TO_DATE:
            raise LockfileStaleError(
                Path(config.manifest_path),
                path,
                reason=current.reason,
            )
    with isolated_package_cache(dry_run):
        plans = []
        for name in env_names:
            plans.append(
                LockfileInstallPlan.prepare(
                    ctx,
                    name,
                    prefix=prefix,
                    target_prefix_override=target_prefix_override,
                    lockfile_data=lockfile_data,
                    replace_existing=force_reinstall,
                    validate_workspace=validate_workspace,
                )
            )
        for index, (name, plan) in enumerate(zip(env_names, plans, strict=True)):
            if index > 0:
                console.print()
            status.message(
                console,
                "Installing",
                "environment",
                name,
                style="bold blue",
                ellipsis=True,
            )
            if not dry_run:
                if force_reinstall:
                    plan.remove_preflight_prefix(ctx)
                plan.execute()
            status.message(
                console,
                "Would install" if dry_run else "Installed",
                "environment",
                name,
            )

    return 0
