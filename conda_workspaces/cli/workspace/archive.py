"""``conda workspace archive`` and ``conda workspace unarchive``."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from rich.console import Console

from ...archive import (
    WorkspaceArchive,
    WorkspaceArchiveInstallResult,
    scan_prefix_references,
)
from ...attestations import SignerPolicy
from ...exceptions import ArchiveError
from .. import status
from . import workspace_manifest_path_from_args

if TYPE_CHECKING:
    from pathlib import Path

    from ...receipts import VerifiedArchiveWorkspace


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
        sign=getattr(args, "sign", False),
        attestation=getattr(args, "attestation", None),
        dry_run=dry_run,
    )

    if args.lock:
        action = "Would update" if dry_run else "Updated"
        status.message(console, action, "lockfile", "conda.lock")
    action = "Would create" if dry_run else "Created"
    status.message(console, action, "archive", str(archive.path))
    if archive.receipt_path is not None:
        status.message(console, action, "receipt", str(archive.receipt_path))
    if archive.attestation_path is not None:
        status.message(
            console,
            action,
            "attestation",
            str(archive.attestation_path),
        )
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
        *,
        verified_workspace: VerifiedArchiveWorkspace | None = None,
    ) -> int:
        if verified_workspace is not None:
            return WorkspaceArchive.install_from_lockfile(
                workspace,
                environment,
                prefix,
                target_prefix_override,
                verified_workspace=verified_workspace,
            )
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
    """Extract a workspace archive."""
    if console is None:
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

    verify_attestation = getattr(args, "verify", False)
    attestation_path = getattr(args, "attestation", None)
    cert_identity = getattr(args, "cert_identity", None)
    cert_oidc_issuer = getattr(args, "cert_oidc_issuer", None)
    has_signer_option = cert_identity is not None or cert_oidc_issuer is not None
    if attestation_path is not None and not verify_attestation:
        raise ArchiveError("--attestation requires --verify.")
    if has_signer_option and not verify_attestation:
        raise ArchiveError("--cert-identity and --cert-oidc-issuer require --verify.")
    expected_signer = None
    if verify_attestation:
        expected_signer = SignerPolicy.from_values(
            cert_identity,
            cert_oidc_issuer,
        )
        if expected_signer is None:
            raise ArchiveError(
                "--verify requires --cert-identity and --cert-oidc-issuer."
            )

    archive = WorkspaceArchive(
        args.archive_path,
        receipt=getattr(args, "receipt", None),
        attestation=(
            attestation_path if attestation_path is not None else verify_attestation
        ),
    )

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
            verify_attestation=verify_attestation,
            expected_signer=expected_signer,
            install_handler=install_from_archive_cli(console),
            dry_run=dry_run,
        )
    else:
        result = archive.extract(
            target=args.target,
            require_sha256=getattr(args, "require_sha256", False),
            prime_cache=not args.no_install,
            verify_attestation=verify_attestation,
            expected_signer=expected_signer,
            dry_run=dry_run,
        )

    if result.verified:
        status.message(console, "Verified", "archive", str(archive.path.name))
    action = "Would extract" if dry_run else "Extracted"
    status.message(console, action, "archive", str(result.target))
    if result.receipt_path is not None:
        status.message(console, "Verified", "receipt", str(result.receipt_path))
    if result.attestation_verified:
        status.message(
            console,
            "Verified",
            "attestation",
            str(result.attestation_path),
        )

    if result.info["has_packages"]:
        console.print(
            f"  Archive includes {result.info['package_count']} bundled packages"
        )
        if result.cache_priming_skipped:
            action = "Would skip" if dry_run else "Skipping"
            console.print(f"  {action} package cache priming without verified receipt")
        elif dry_run and not args.no_install:
            status.message(
                console,
                "Would prime",
                "packages",
                str(result.primed_packages),
                detail="into conda cache",
            )
        elif result.primed_packages > 0:
            status.message(
                console,
                "Primed",
                "packages",
                str(result.primed_packages),
                detail="into conda cache",
            )

    if args.install:
        assert isinstance(result, WorkspaceArchiveInstallResult)
        if dry_run:
            name = getattr(args, "environment", None) or "workspace environments"
            status.message(console, "Would install", "environment", name)
            return 0
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
