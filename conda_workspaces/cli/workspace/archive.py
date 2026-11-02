"""``conda workspace archive`` and ``conda workspace unarchive``."""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import nullcontext, redirect_stdout
from typing import TYPE_CHECKING

from conda.base.constants import DEFAULT_JSON_REPORTER_BACKEND
from conda.base.context import context as conda_context
from rich.console import Console

from ...archive import (
    WorkspaceArchive,
    WorkspaceArchiveInstallResult,
    scan_prefix_references,
)
from ...exceptions import ArchiveError
from .. import status
from . import workspace_manifest_path_from_args

if TYPE_CHECKING:
    from pathlib import Path


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
    console.print(
        f"  [dim]staging prefix:[/dim] {status.escape_for_console(install_prefix)}"
    )
    console.print(
        f"  [dim]runtime prefix:[/dim] {status.escape_for_console(runtime_prefix)}"
    )
    for path in matches:
        try:
            display_path = path.relative_to(install_prefix)
        except ValueError:
            display_path = path
        console.print(f"  [dim]- {status.escape_for_console(display_path)}[/dim]")
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

    dry_run = bool(getattr(args, "dry_run", False))
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
        workspace=workspace_manifest_path_from_args(args),
        output=args.output,
        lock=args.lock,
        bundle=args.bundle,
        exclude=tuple(args.exclude or ()),
        receipt=getattr(args, "receipt", None),
        dry_run=dry_run,
    )

    if args.lock:
        action = "Would update" if dry_run else "Updated"
        status.message(console, action, "lockfile", "conda.lock")
    action = "Would create" if dry_run else "Created"
    status.message(console, action, "archive", str(archive.path))
    if archive.receipt_path is not None:
        status.message(console, action, "receipt", str(archive.receipt_path))
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
            manifest_file=WorkspaceArchive.resolve_extracted_manifest(workspace),
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
    """Retrieve and extract a workspace archive, optionally installing it."""
    json_output = (
        bool(getattr(args, "json", False))
        or conda_context.console == DEFAULT_JSON_REPORTER_BACKEND
    )
    if json_output:
        console = Console(
            file=io.StringIO(),
            highlight=False,
            force_terminal=False,
            no_color=True,
        )
    elif console is None:
        console = Console(highlight=False)

    dry_run = bool(getattr(args, "dry_run", False))
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

    receipt = getattr(args, "receipt", None)
    if getattr(args, "require_sha256", False) and not receipt:
        raise ArchiveError("--require-sha256 requires --receipt.")
    source_label = WorkspaceArchive.source_label(args.archive_path)
    receipt_label = None
    if receipt is True:
        receipt_label = source_label + ".receipt.json"
    elif receipt:
        receipt_label = WorkspaceArchive.source_label(receipt)

    with (
        redirect_stdout(io.StringIO()) if json_output else nullcontext(),
        WorkspaceArchive.open_source(
            args.archive_path,
            receipt=receipt,
            allow_large_archive=getattr(args, "allow_large_archive", False),
        ) as archive,
    ):
        preparing = "Inspecting" if dry_run else "Extracting"
        status.message(
            console,
            preparing,
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
                dry_run=dry_run,
                allow_large_archive=getattr(args, "allow_large_archive", False),
            )
        else:
            result = archive.extract(
                target=args.target,
                require_sha256=getattr(args, "require_sha256", False),
                dry_run=dry_run,
                allow_large_archive=getattr(args, "allow_large_archive", False),
            )

        if result.verified:
            status.message(console, "Verified", "archive", str(archive.path.name))
        action = "Would extract" if dry_run else "Extracted"
        status.message(console, action, "archive", str(result.target))
        if result.verified:
            status.message(console, "Verified", "receipt", str(receipt_label))

        if result.info["has_packages"]:
            console.print(
                f"  Archive includes {result.info['package_count']} bundled packages"
            )
            if args.install and result.cache_priming_skipped:
                action = "Would skip" if dry_run else "Skipping"
                reason = (
                    "without verified receipt"
                    if not result.verified
                    else "for this install"
                )
                console.print(f"  {action} bundled packages {reason}")
            elif args.install and result.primed_packages > 0:
                status.message(
                    console,
                    "Would stage" if dry_run else "Staged",
                    "packages",
                    str(result.primed_packages),
                    detail="for this install",
                )

        return_code = 0
        if args.install:
            assert isinstance(result, WorkspaceArchiveInstallResult)
            return_code = result.return_code
            if dry_run:
                name = getattr(args, "environment", None) or "workspace environments"
                status.message(console, "Would install", "environment", name)
            elif (
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

    if json_output:
        payload = {
            "success": return_code == 0,
            "source": source_label,
            "target": str(result.target),
            "receipt": receipt_label,
            "verified": result.verified,
            "dry_run": dry_run,
            "installed": bool(args.install and not dry_run and return_code == 0),
            "environment": getattr(args, "environment", None),
            "primed_packages": result.primed_packages,
            "cache_priming_skipped": result.cache_priming_skipped,
            "return_code": return_code,
        }
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
    return return_code
