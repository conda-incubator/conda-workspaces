"""``conda workspace attest`` / ``verify`` - manage workspace attestations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from rich.console import Console

from ...lockfile import lockfile_path
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse


def execute_attest(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Sign the current workspace manifest and lockfile."""
    if console is None:
        console = Console(highlight=False)

    config, ctx = workspace_context_from_args(args)
    workspace_lockfile = lockfile_path(ctx)

    from ...attestations import write_workspace_attestation

    written = write_workspace_attestation(
        root=ctx.root,
        manifest_path=Path(config.manifest_path),
        lockfile_path=workspace_lockfile,
        bundle_path=getattr(args, "attestation", None),
        identity_token=getattr(args, "identity_token", None),
    )
    if not conda_context.json and not getattr(args, "json", False):
        console.print(f"[bold cyan]Signed[/bold cyan] [bold]{written.name}[/bold]")
    return 0


def execute_verify(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Verify the current workspace manifest and lockfile attestation."""
    if console is None:
        console = Console(highlight=False)

    config, ctx = workspace_context_from_args(args)
    workspace_lockfile = lockfile_path(ctx)
    bundle_path: Path | None = getattr(args, "attestation", None)

    from ...attestations import (
        default_attestation_path,
        trust_identities_from_cli,
        verify_workspace_attestation,
    )

    identities = trust_identities_from_cli(
        getattr(args, "cert_identity", None),
        getattr(args, "cert_oidc_issuer", None),
    )
    verify_workspace_attestation(
        root=ctx.root,
        manifest_path=Path(config.manifest_path),
        lockfile_path=workspace_lockfile,
        bundle_path=bundle_path,
        identities=identities,
    )
    if getattr(args, "json", False):
        console.print_json(
            json.dumps(
                {
                    "verified": True,
                    "manifest": str(Path(config.manifest_path)),
                    "lockfile": str(workspace_lockfile),
                    "attestation": str(
                        bundle_path or default_attestation_path(workspace_lockfile)
                    ),
                }
            )
        )
    else:
        console.print(
            "[bold cyan]Verified[/bold cyan] [bold]lockfile attestation[/bold]"
        )
    return 0
