"""``conda workspace lock`` — solve and generate lockfiles."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from conda.exceptions import CondaValueError
from rich.console import Console

from ...attestations import (
    AttestationOutput,
    default_attestation_path,
    sign_workspace_snapshot,
)
from ...exceptions import EnvironmentNotFoundError
from ...lockfile import generate_lockfile, lockfile_path, merge_lockfiles
from ...manifests import find_parser
from ...paths import output_paths_collide
from ...publication import WorkspacePublication
from ...resolver import known_platforms, resolve_all_environments, resolve_environment
from .. import status
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse

    from ...exceptions import SolveError


def execute_lock(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Solve workspace environments and write ``conda.lock``."""
    if console is None:
        console = Console(highlight=False)
    requested_manifest_path = getattr(args, "manifest_file", None)
    if requested_manifest_path is not None:
        WorkspacePublication.validate_manifest_path(Path(requested_manifest_path))
    config, ctx = workspace_context_from_args(args, for_mutation=True)

    env_name = getattr(args, "environment", None)
    requested_platforms: list[str] | None = getattr(args, "platform", None) or None
    skip_unsolvable: bool = bool(getattr(args, "skip_unsolvable", False))
    merge_patterns: list[str] | None = getattr(args, "merge", None) or None
    output_path: Path | None = getattr(args, "output", None)
    dry_run: bool = bool(getattr(args, "dry_run", False))
    sign: bool = bool(getattr(args, "sign", False))
    attestation_path: Path | None = getattr(args, "attestation", None)
    canonical_path = lockfile_path(ctx)
    if attestation_path is not None and not sign:
        raise CondaValueError("--attestation requires --sign.")
    if sign and (env_name or requested_platforms or skip_unsolvable):
        raise CondaValueError("--sign only supports the complete canonical conda.lock.")
    if (
        sign
        and output_path is not None
        and output_path.resolve(strict=False) != canonical_path.resolve(strict=False)
    ):
        raise CondaValueError(
            "--sign cannot be combined with a noncanonical --output path."
        )
    WorkspacePublication.validate_manifest_path(Path(config.manifest_path))
    publication = (
        None if dry_run else WorkspacePublication.from_current_manifest(ctx, "lock")
    )
    manifest_path = Path(config.manifest_path)
    manifest_format = find_parser(manifest_path).exporter_format
    sidecar = attestation_path or default_attestation_path(canonical_path)
    if sign and dry_run:
        AttestationOutput.prepare(
            sidecar,
            protected_paths=(manifest_path, canonical_path),
        )

    def publish_lockfile(content: str) -> None:
        if publication is None:
            raise RuntimeError("Lockfile publication is unavailable during dry-run")
        if not sign:
            publication.publish_lockfile(content)
            return
        snapshot = publication.snapshot_with_lockfile_bytes(
            manifest_format,
            content.encode("utf-8"),
        )
        output = AttestationOutput.prepare(
            sidecar,
            protected_paths=(snapshot.manifest_path, snapshot.lockfile_path),
            directory_descriptor=(
                publication.guarded_root_descriptor
                if sidecar.parent.absolute() == ctx.root.absolute()
                else None
            ),
            create_parent=True,
        )
        bundle_json = sign_workspace_snapshot(snapshot)
        with publication.reversible_lockfile_publication(content):
            publication.validate_snapshot(snapshot)
            with output.reversible_write(bundle_json):
                publication.validate_snapshot(snapshot)

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
            console.print(f"  [dim]<-[/dim] {status.escape_for_console(fragment)}")
        publication_guard = (
            publication.guard() if publication is not None else nullcontext()
        )
        with publication_guard:
            merge_lockfiles(
                fragments,
                ctx,
                dry_run=dry_run,
                publish_lockfile=(
                    publish_lockfile if publication is not None else None
                ),
            )
        action = "Would update" if dry_run else "Updated"
        console.print(f"[bold cyan]{action}[/bold cyan] [bold]conda.lock[/bold]")
        return 0

    if output_path is None and (env_name or requested_platforms or skip_unsolvable):
        raise CondaValueError(
            "--output is required with --environment, --platform, or --skip-unsolvable."
        )

    if env_name:
        if env_name not in config.environments:
            raise EnvironmentNotFoundError(
                env_name,
                list(config.environments.keys()),
            )
        resolved = resolve_environment(config, env_name)
        resolved_envs = {env_name: resolved}
    else:
        resolved_envs = resolve_all_environments(config)

    platforms: tuple[str, ...] | None = None
    if requested_platforms:
        # Catch --platform typos (e.g. "lixux-64") before the solver
        # burns any time. An environment filter narrows validation to
        # that environment. Otherwise use the full workspace + feature
        # set surfaced via resolved_envs.
        known = (
            set(resolved_envs[env_name].platforms)
            if env_name
            else known_platforms(config, resolved_envs.values())
        )
        resolved_platforms: list[str] = []
        for platform in requested_platforms:
            resolved_platform = config.resolve_platform_name(platform, sorted(known))
            if resolved_platform not in resolved_platforms:
                resolved_platforms.append(resolved_platform)
        platforms = tuple(resolved_platforms)

    def _progress(env: str, platform: str) -> None:
        console.print(
            "[bold blue]Locking[/bold blue] "
            f"[bold]{status.escape_for_console(env)}[/bold] for "
            f"[bold]{status.escape_for_console(platform)}[/bold][dim]...[/dim]"
        )

    def _on_skip(env: str, platform: str, exc: SolveError) -> None:
        console.print(
            "[bold yellow]Skipping[/bold yellow] "
            f"[bold]{status.escape_for_console(env)}[/bold] on "
            f"[bold]{status.escape_for_console(platform)}[/bold][dim]:[/dim] "
            f"{status.escape_for_console(exc.reason)}"
        )

    updating_label = output_path.name if output_path is not None else "conda.lock"
    console.print(
        "[bold blue]Updating[/bold blue] "
        f"[bold]{status.escape_for_console(updating_label)}[/bold][dim]...[/dim]"
    )
    path_equal = output_path is None or output_path.resolve(
        strict=False
    ) == canonical_path.resolve(strict=False)
    if (
        output_path is not None
        and not path_equal
        and output_paths_collide(output_path, canonical_path)
    ):
        raise CondaValueError(
            "--output cannot be a hardlink alias of the workspace conda.lock."
        )
    canonical_output = path_equal
    publication_guard = (
        publication.guard()
        if publication is not None and canonical_output
        else nullcontext()
    )
    with publication_guard:
        generate_lockfile(
            ctx,
            resolved_envs,
            config=config,
            platforms=platforms,
            progress=_progress,
            skip_unsolvable=skip_unsolvable,
            on_skip=_on_skip if skip_unsolvable else None,
            output_path=output_path,
            dry_run=dry_run,
            publish_lockfile=(
                publish_lockfile
                if publication is not None and canonical_output
                else None
            ),
        )
    target_label = output_path.name if output_path is not None else "conda.lock"
    action = "Would update" if dry_run else "Updated"
    console.print(
        f"[bold cyan]{action}[/bold cyan] "
        f"[bold]{status.escape_for_console(target_label)}[/bold]"
    )

    return 0
