"""``conda workspace archive`` and ``conda workspace unarchive``."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from ...archive import (
    WorkspaceArchive,
    extract_verified_archive,
    file_contains_bytes,
    is_absolute_runtime_prefix,
    receipt_environment_prefixes,
    resolve_receipt_path,
    runtime_prefix_relative_path,
    scan_prefix_references,
)
from ...exceptions import ArchiveError
from .. import status

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "execute_archive",
    "execute_unarchive",
    "extract_verified_archive",
    "file_contains_bytes",
    "is_absolute_runtime_prefix",
    "receipt_environment_prefixes",
    "resolve_receipt_path",
    "runtime_prefix_relative_path",
    "scan_prefix_references",
)


def warn_staging_prefix_references(
    console: Console,
    *,
    install_prefix: Path,
    runtime_prefix: str,
    matches: tuple[Path, ...] | None = None,
    truncated: bool = False,
) -> None:
    """Warn when a staged install still contains the physical staging prefix."""
    if matches is None:
        found, truncated = scan_prefix_references(install_prefix, install_prefix)
        matches = tuple(found)
    if not matches:
        return

    console.print(
        "[bold yellow]Warning:[/bold yellow] "
        "installed files still reference the staging prefix"
    )
    console.print(f"  [dim]staging prefix:[/dim] {escape(str(install_prefix))}")
    console.print(f"  [dim]runtime prefix:[/dim] {escape(str(runtime_prefix))}")
    for path in matches:
        try:
            display_path = path.relative_to(install_prefix)
        except ValueError:
            display_path = path
        console.print(f"  [dim]- {escape(str(display_path))}[/dim]")
    if truncated:
        console.print("  [dim]additional matches omitted[/dim]")


def execute_archive(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Create a workspace archive."""
    if console is None:
        console = Console(highlight=False)

    if args.lock:
        status.message(
            console,
            "Locking",
            "workspace",
            "environments",
            style="bold blue",
            ellipsis=True,
        )
    archive = WorkspaceArchive.create(
        workspace=getattr(args, "file", None),
        output=args.output,
        lock=args.lock,
        bundle=args.bundle,
        exclude=tuple(args.exclude or ()),
        receipt=getattr(args, "receipt", None),
    )

    if args.lock:
        status.message(console, "Updated", "lockfile", "conda.lock")
    status.message(console, "Created", "archive", str(archive.path))
    if archive.receipt_path is not None:
        status.message(console, "Created", "receipt", str(archive.receipt_path))
    return 0


def install_from_archive_cli(
    console: Console,
):
    """Return an install handler that preserves the CLI install path."""

    def install(
        workspace: Path,
        environment: str | None,
        prefix: Path | None,
        target_prefix_override: str | None,
    ) -> int:
        from .install import execute_install

        install_args = argparse.Namespace(
            file=str(workspace),
            environment=environment,
            force_reinstall=False,
            locked=True,
            frozen=False,
            dry_run=False,
            json=False,
            prefix=prefix,
            target_prefix_override=target_prefix_override,
        )
        return execute_install(install_args, console=console)

    return install


def execute_unarchive(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Extract a workspace archive."""
    if console is None:
        console = Console(highlight=False)

    if getattr(args, "prefix", None) is not None and not args.install:
        raise ArchiveError(
            "--prefix requires --install.",
            hints=["Pass --install when installing to an explicit prefix."],
        )
    if getattr(args, "dest", None) is not None and not args.install:
        raise ArchiveError(
            "--dest requires --install.",
            hints=["Pass --install when using a staging destination."],
        )

    archive = WorkspaceArchive(
        args.archive_path,
        receipt=getattr(args, "receipt", None),
    )
    status.message(
        console,
        "Extracting",
        "archive",
        str(archive.path.name),
        style="bold blue",
        ellipsis=True,
    )

    if args.install:
        result = archive.install(
            target=args.target,
            environment=getattr(args, "environment", None),
            prefix=getattr(args, "prefix", None),
            dest=getattr(args, "dest", None),
            require_sha256=getattr(args, "require_sha256", False),
            prime_cache=not args.no_install,
            install_handler=install_from_archive_cli(console),
        )
    else:
        result = archive.extract(
            target=args.target,
            require_sha256=getattr(args, "require_sha256", False),
            prime_cache=not args.no_install,
        )

    if result.verified:
        status.message(console, "Verified", "archive", str(archive.path.name))
    status.message(console, "Extracted", "archive", str(result.target))
    if result.verified:
        status.message(console, "Verified", "receipt", str(result.receipt_path))

    if result.info["has_packages"]:
        console.print(
            f"  Archive includes {result.info['package_count']} bundled packages"
        )
        if result.cache_priming_skipped:
            console.print("  Skipping package cache priming without verified receipt")
        elif result.primed_packages > 0:
            status.message(
                console,
                "Primed",
                "packages",
                str(result.primed_packages),
                detail="into conda cache",
            )

    if args.install:
        if (
            result.return_code == 0
            and result.install_prefix is not None
            and result.runtime_prefix is not None
        ):
            warn_staging_prefix_references(
                console,
                install_prefix=result.install_prefix,
                runtime_prefix=result.runtime_prefix,
                matches=result.prefix_reference_matches,
                truncated=result.prefix_reference_matches_truncated,
            )
        return result.return_code

    return 0
