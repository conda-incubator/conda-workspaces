"""``conda workspace attest`` and ``verify`` command handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from rich.console import Console

from ...attestations import (
    AttestationOutput,
    SignerPolicy,
    WorkspaceAttestation,
    default_attestation_path,
    read_attestation_bundle,
    sign_workspace_snapshot,
    verify_workspace_snapshot,
)
from ...lockfile import MAX_LOCKFILE_BYTES, lockfile_path
from ...manifests import find_parser
from ...manifests.base import MAX_MANIFEST_BYTES
from ...paths import read_regular_file_bytes
from ...publication import WorkspacePublication, WorkspaceSnapshot
from .. import status
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse


def execute_attest(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Sign the current workspace manifest and canonical lockfile."""
    if console is None:
        console = Console(highlight=False)

    config, ctx = workspace_context_from_args(args, for_mutation=True)
    manifest_path = Path(config.manifest_path)
    workspace_lockfile = lockfile_path(ctx)
    sidecar = Path(
        getattr(args, "attestation", None)
        or default_attestation_path(workspace_lockfile)
    )
    dry_run = bool(getattr(args, "dry_run", False))
    manifest_format = find_parser(manifest_path).exporter_format

    if dry_run:
        output = AttestationOutput.prepare(
            sidecar,
            protected_paths=(manifest_path, workspace_lockfile),
        )
        manifest_bytes = read_regular_file_bytes(
            manifest_path,
            maximum_bytes=MAX_MANIFEST_BYTES,
            label="workspace manifest",
        )
        lockfile_bytes = read_regular_file_bytes(
            workspace_lockfile,
            maximum_bytes=MAX_LOCKFILE_BYTES,
            label="workspace lockfile",
        )
        snapshot = WorkspaceSnapshot.from_bytes(
            root=ctx.root,
            manifest_path=manifest_path,
            manifest_bytes=manifest_bytes,
            manifest_format=manifest_format,
            lockfile_path=workspace_lockfile,
            lockfile_bytes=lockfile_bytes,
        )
        WorkspaceAttestation.from_snapshot(snapshot)
        written = output.path
    else:
        publication = WorkspacePublication.from_current_manifest(ctx, "attestation")
        with publication.guard():
            snapshot = publication.snapshot(manifest_format)
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
            publication.validate_snapshot(snapshot)
            with output.reversible_write(bundle_json) as written:
                publication.validate_snapshot(snapshot)

    if bool(getattr(args, "json", False) or conda_context.json):
        console.print_json(
            json.dumps(
                {
                    "success": True,
                    "sidecar": str(written),
                }
            )
        )
    else:
        action = "Would sign" if dry_run else "Signed"
        console.print(
            f"[bold cyan]{action}[/bold cyan] workspace to "
            f"[bold]{status.escape_for_console(written)}[/bold]"
        )
    return 0


def execute_verify(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Verify the current workspace manifest and canonical lockfile."""
    if console is None:
        console = Console(highlight=False)

    config, ctx = workspace_context_from_args(args)
    manifest_path = Path(config.manifest_path)
    workspace_lockfile = lockfile_path(ctx)
    sidecar = Path(
        getattr(args, "attestation", None)
        or default_attestation_path(workspace_lockfile)
    )
    signer_policy = SignerPolicy.from_values(
        getattr(args, "cert_identity", None),
        getattr(args, "cert_oidc_issuer", None),
    )
    manifest_format = find_parser(manifest_path).exporter_format
    publication = WorkspacePublication.from_current_manifest(ctx, "verification")

    with publication.guard():
        snapshot = publication.snapshot(manifest_format)
        bundle_bytes = read_attestation_bundle(sidecar)
        verification = verify_workspace_snapshot(
            bundle_bytes,
            snapshot,
            signer_policy,
        )
        if signer_policy is not None:
            verification.require_authorized()
        publication.validate_snapshot(snapshot)

    if bool(getattr(args, "json", False) or conda_context.json):
        console.print_json(
            json.dumps(
                {
                    "success": True,
                    "verified": True,
                    "authorized": verification.authorized,
                    "sidecar": str(sidecar),
                    "manifest": str(snapshot.manifest_path),
                    "lockfile": str(snapshot.lockfile_path),
                    "predicate_type": verification.predicate_type,
                    "signer": {
                        **verification.signer.to_dict(),
                        "timestamps": list(verification.timestamps),
                    },
                }
            )
        )
    else:
        authorization = (
            "authorized" if verification.authorized else "authorization not evaluated"
        )
        console.print(
            "[bold cyan]Verified[/bold cyan] workspace attestation from "
            f"[bold]{status.escape_for_console(verification.signer.identity)}[/bold] "
            f"([dim]{status.escape_for_console(authorization)}[/dim])"
        )
    return 0
