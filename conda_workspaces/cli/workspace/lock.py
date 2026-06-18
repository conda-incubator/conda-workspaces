"""``conda workspace lock`` — solve and generate lockfiles."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conda.exceptions import CondaValueError
from rich.console import Console

from ...exceptions import EnvironmentNotFoundError
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
    sign: bool = bool(getattr(args, "sign", False))
    attestation_path: Path | None = getattr(args, "attestation", None)
    identity_token: str | None = getattr(args, "identity_token", None)

    if (attestation_path is not None or identity_token is not None) and not sign:
        raise CondaValueError("--attestation and --identity-token require --sign.")

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
        lock_path = merge_lockfiles(fragments, ctx)
        target_label = "conda.lock"
    else:
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
            resolved_platforms: list[str] = []
            for platform in requested_platforms:
                resolved_platform = config.resolve_platform_name(
                    platform, sorted(known)
                )
                if resolved_platform not in resolved_platforms:
                    resolved_platforms.append(resolved_platform)
            platforms = tuple(resolved_platforms)

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
            "[bold blue]Updating[/bold blue] "
            f"[bold]{updating_label}[/bold][dim]...[/dim]"
        )
        lock_path = generate_lockfile(
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
    if sign:
        from ...attestations import write_workspace_attestation

        written = write_workspace_attestation(
            root=ctx.root,
            manifest_path=Path(config.manifest_path),
            lockfile_path=lock_path,
            bundle_path=attestation_path,
            identity_token=identity_token,
        )
        console.print(f"[bold cyan]Signed[/bold cyan] [bold]{written.name}[/bold]")

    return 0
