"""``conda workspace lock`` — solve and generate lockfiles."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from ...exceptions import EnvironmentNotFoundError, PlatformError
from ...lockfile import generate_lockfile
from ...resolver import resolve_all_environments, resolve_environment
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse

    from ...resolver import ResolvedEnvironment


def _validate_platforms(
    requested: list[str] | None,
    declared: list[str],
    resolved_envs: dict[str, ResolvedEnvironment],
) -> tuple[str, ...] | None:
    """Return the platform tuple to pass to :func:`generate_lockfile`.

    *requested* is the raw ``--platform`` value from argparse (or
    ``None`` when omitted).  Each requested platform must be declared
    by the workspace *and* by at least one of the resolved
    environments, otherwise we raise :class:`PlatformError` before
    touching the solver.
    """
    if not requested:
        return None
    env_platforms: set[str] = set()
    for resolved in resolved_envs.values():
        env_platforms.update(resolved.platforms or declared or [])
    known = set(declared) | env_platforms
    for platform in requested:
        if platform not in known:
            raise PlatformError(platform, sorted(known))
    return tuple(requested)


def execute_lock(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Solve workspace environments and write ``conda.lock``."""
    if console is None:
        console = Console(highlight=False)
    config, ctx = workspace_context_from_args(args)

    env_name = getattr(args, "environment", None)
    requested_platforms: list[str] | None = getattr(args, "platform", None) or None

    if env_name:
        if env_name not in config.environments:
            raise EnvironmentNotFoundError(
                env_name,
                list(config.environments.keys()),
            )
        resolved = resolve_environment(config, env_name, ctx.platform)
        resolved_envs = {env_name: resolved}
    else:
        resolved_envs = resolve_all_environments(config, ctx.platform)

    platforms = _validate_platforms(
        requested_platforms,
        list(config.platforms),
        resolved_envs,
    )

    def _progress(env: str, platform: str) -> None:
        console.print(
            f"[bold blue]Locking[/bold blue] [bold]{env}[/bold]"
            f" for [bold]{platform}[/bold][dim]...[/dim]"
        )

    console.print(
        "[bold blue]Updating[/bold blue] [bold]conda.lock[/bold][dim]...[/dim]"
    )
    generate_lockfile(ctx, resolved_envs, platforms=platforms, progress=_progress)
    console.print("[bold cyan]Updated[/bold cyan] [bold]conda.lock[/bold]")

    return 0
