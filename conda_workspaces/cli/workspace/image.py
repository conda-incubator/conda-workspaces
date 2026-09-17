"""``conda workspace image`` — build a runnable workspace environment image."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from ...exceptions import CondaWorkspacesError
from ...image import WorkspaceImage
from .. import status
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse


def execute_image(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Build an image or display its recipe and inputs without building."""
    command = tuple(args.cmd)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise CondaWorkspacesError(
            "No image command specified.",
            hints=[
                (
                    "Usage: conda workspace image -e ENV --platform PLATFORM "
                    "-t IMAGE --load -- COMMAND [ARGS...]"
                )
            ],
        )

    config, ctx = workspace_context_from_args(args)
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment=args.environment,
        platform=args.platform,
        command=command,
        tags=tuple(args.tag or ()),
        output=args.output,
        load=args.load,
        push=args.push,
        base_image=args.base_image,
        builder=args.builder,
    )
    dry_run = bool(args.dry_run)
    result = image.preview() if dry_run else image.build()

    if console is None:
        console = Console(highlight=False)
    if args.json:
        console.print_json(data=result)
        return 0

    if dry_run:
        console.print("Containerfile:")
        console.print(result["recipe"], markup=False, highlight=False)
        console.print("Workspace files:")
        files = result["files"]
        if isinstance(files, list | tuple):
            for file in files:
                console.print(f"  {file}", markup=False, highlight=False)

    if args.output is not None:
        verb = "Would write" if dry_run else "Wrote"
        status.message(console, verb, "OCI image archive", str(args.output))
    elif args.load:
        verb = "Would load" if dry_run else "Loaded"
        status.message(console, verb, "image", ", ".join(args.tag or ()))
    else:
        verb = "Would push" if dry_run else "Pushed"
        status.message(console, verb, "image", ", ".join(args.tag or ()))
    return 0
