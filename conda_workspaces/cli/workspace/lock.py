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

    from ...exceptions import SolveError


def execute_lock(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Solve workspace environments and write ``conda.lock``."""
    if console is None:
        console = Console(highlight=False)
    config, ctx = workspace_context_from_args(args)

    env_name = getattr(args, "environment", None)
    requested_platforms: list[str] | None = getattr(args, "platform", None) or None
    skip_unsolvable: bool = bool(getattr(args, "skip_unsolvable", False))

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

    platforms: tuple[str, ...] | None = None
    if requested_platforms:
        # A requested platform must be declared either by the workspace
        # or by at least one of the resolved environments, so CI catches
        # typos before the solver burns any time.
        known: set[str] = set(config.platforms)
        for resolved in resolved_envs.values():
            known.update(resolved.platforms or config.platforms or [])
        for platform in requested_platforms:
            if platform not in known:
                raise PlatformError(platform, sorted(known))
        platforms = tuple(requested_platforms)

    def _progress(env: str, platform: str) -> None:
        console.print(
            f"[bold blue]Locking[/bold blue] [bold]{env}[/bold]"
            f" for [bold]{platform}[/bold][dim]...[/dim]"
        )

    def _on_skip(env: str, platform: str, exc: SolveError) -> None:
        console.print(
            f"[bold yellow]Skipping[/bold yellow] [bold]{env}[/bold]"
            f" on [bold]{platform}[/bold][dim]:[/dim] {exc.reason}"
        )

    console.print(
        "[bold blue]Updating[/bold blue] [bold]conda.lock[/bold][dim]...[/dim]"
    )
    generate_lockfile(
        ctx,
        resolved_envs,
        platforms=platforms,
        progress=_progress,
        skip_unsolvable=skip_unsolvable,
        on_skip=_on_skip if skip_unsolvable else None,
    )
    console.print("[bold cyan]Updated[/bold cyan] [bold]conda.lock[/bold]")

    return 0
