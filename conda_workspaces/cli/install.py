"""``conda workspace install`` — create or update workspace environments."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..context import WorkspaceContext
from ..envs import install_environment
from ..lockfile import generate_lockfile, install_from_lockfile, lockfile_path
from ..parsers import detect_and_parse
from ..resolver import resolve_all_environments, resolve_environment

if TYPE_CHECKING:
    import argparse

    from ..models import WorkspaceConfig


def execute_install(args: argparse.Namespace) -> int:
    """Install (create/update) workspace environments."""
    manifest_path = getattr(args, "file", None)
    _, config = detect_and_parse(manifest_path)
    ctx = WorkspaceContext(config)

    env_name = getattr(args, "environment", None)
    force = getattr(args, "force_reinstall", False)
    dry_run = getattr(args, "dry_run", False)
    locked = getattr(args, "locked", False)
    frozen = getattr(args, "frozen", False)

    if frozen:
        return _install_from_lockfile(ctx, config, env_name)

    if locked:
        _check_lockfile_freshness(ctx, config)
        return _install_from_lockfile(ctx, config, env_name)

    if env_name:
        resolved = resolve_environment(config, env_name, ctx.platform)
        print(f"Installing environment '{env_name}'...")
        install_environment(ctx, resolved, force_reinstall=force, dry_run=dry_run)
        if not dry_run:
            generate_lockfile(ctx, {env_name: resolved})
        print(f"Environment '{env_name}' is ready at {ctx.env_prefix(env_name)}")
    else:
        resolved_all = resolve_all_environments(config, ctx.platform)
        for name, resolved in resolved_all.items():
            print(f"Installing environment '{name}'...")
            install_environment(ctx, resolved, force_reinstall=force, dry_run=dry_run)
            print(f"  -> {ctx.env_prefix(name)}")
        if not dry_run:
            generate_lockfile(ctx, resolved_all)
        print(f"\n{len(resolved_all)} environment(s) installed.")

    return 0


def _check_lockfile_freshness(ctx: WorkspaceContext, config: WorkspaceConfig) -> None:
    """Raise if the lockfile is missing or older than the manifest."""
    from ..exceptions import LockfileNotFoundError, LockfileStaleError

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
) -> int:
    """Install environments from existing lockfiles (no solving)."""
    if env_name:
        print(f"Installing environment '{env_name}' from lockfile...")
        install_from_lockfile(ctx, env_name)
        print(f"Environment '{env_name}' is ready at {ctx.env_prefix(env_name)}")
    else:
        env_names = list(config.environments)
        for name in env_names:
            print(f"Installing environment '{name}' from lockfile...")
            install_from_lockfile(ctx, name)
            print(f"  -> {ctx.env_prefix(name)}")
        print(f"\n{len(env_names)} environment(s) installed from lockfiles.")

    return 0
