"""Handler for ``conda workspace import``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from conda.exceptions import CondaSystemExit, DryRunExit
from conda.reporters import confirm_yn
from rich.console import Console
from rich.syntax import Syntax

from ...importers import find_importer
from .. import status

if TYPE_CHECKING:
    import argparse


def execute_import(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Execute the ``conda workspace import`` subcommand."""
    if console is None:
        console = Console(highlight=False)

    source: Path = args.file
    if not source.exists():
        status.print_error(console, FileNotFoundError(str(source)))
        return 1

    quiet = getattr(args, "quiet", False)
    dry_run = getattr(args, "dry_run", False)
    output: Path = getattr(args, "output", None) or Path("conda.toml")

    importer = find_importer(source)
    if not quiet:
        status.message(
            console,
            "Reading",
            "manifest",
            source.name,
            style="bold blue",
            ellipsis=True,
        )

    doc = importer.convert(source)
    text = tomlkit.dumps(doc)

    if not quiet:
        status.message(
            console,
            "Detected",
            "format",
            importer.label,
        )

    if dry_run:
        if console.is_terminal:
            console.print(Syntax(text, "toml", theme="ansi_dark"))
        else:
            print(text, end="", file=console.file)
        raise DryRunExit()

    if output.is_file():
        try:
            confirm_yn(f"Overwrite {output}?")
        except (CondaSystemExit, DryRunExit):
            return 0

    output.write_text(text, encoding="utf-8")
    if not quiet:
        parent = str(output.parent)
        status.message(
            console,
            "Wrote",
            "workspace",
            output.name,
            detail=parent if parent != "." else None,
        )

    return 0
