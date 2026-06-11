"""``conda workspace lock`` — solve and generate lockfiles."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conda.exceptions import CondaValueError
from rich.console import Console

from ...exceptions import EnvironmentNotFoundError, PlatformError
from ...lockfile import generate_lockfile, merge_lockfiles
from ...resolver import known_platforms, resolve_all_environments, resolve_environment
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
    merge_patterns: list[str] | None = getattr(args, "merge", None) or None
    output_path: Path | None = getattr(args, "output", None)

    if merge_patterns:
        if env_name or requested_platforms or skip_unsolvable or output_path:
            raise CondaValueError(
                "--merge cannot be combined with --environment, --platform,"
                " --skip-unsolvable, or --output."
            )
        # Expand --merge values (plain paths or glob patterns) relative
        # to the current working directory, deduplicating while
        # preserving first-seen order so the merged output stays stable
        # when a user passes overlapping globs.
        cwd = Path.cwd()
        fragments: list[Path] = []
        seen: set[Path] = set()
        for pattern in merge_patterns:
            raw = Path(pattern)
            if any(ch in pattern for ch in "*?["):
                if raw.is_absolute():
                    anchor = Path(raw.anchor)
                    matches = sorted(anchor.glob(str(raw.relative_to(raw.anchor))))
                else:
                    matches = sorted(cwd.glob(pattern))
            else:
                matches = [raw if raw.is_absolute() else cwd / raw]
            for match in matches:
                resolved = match.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    fragments.append(match)
        if not fragments:
            raise CondaValueError(
                "--merge matched no files; check the pattern and try again."
            )
        console.print(
            "[bold blue]Merging[/bold blue]"
            f" [bold]{len(fragments)}[/bold] lockfile fragment"
            f"{'s' if len(fragments) != 1 else ''}"
            "[dim]...[/dim]"
        )
        for fragment in fragments:
            console.print(f"  [dim]<-[/dim] {fragment}")
        merge_lockfiles(fragments, ctx)
        console.print("[bold cyan]Updated[/bold cyan] [bold]conda.lock[/bold]")
        return 0

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
        # Catch --platform typos (e.g. "lixux-64") before the solver
        # burns any time by validating against the full reachable
        # platform set — workspace + feature declarations surfaced via
        # resolved_envs.
        known = known_platforms(config, resolved_envs.values())
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

    updating_label = output_path.name if output_path is not None else "conda.lock"
    console.print(
        f"[bold blue]Updating[/bold blue] [bold]{updating_label}[/bold][dim]...[/dim]"
    )
    generate_lockfile(
        ctx,
        resolved_envs,
        config=config,
        platforms=platforms,
        progress=_progress,
        skip_unsolvable=skip_unsolvable,
        on_skip=_on_skip if skip_unsolvable else None,
        output_path=output_path,
    )
    target_label = output_path.name if output_path is not None else "conda.lock"
    console.print(f"[bold cyan]Updated[/bold cyan] [bold]{target_label}[/bold]")

    return 0
