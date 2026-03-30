"""Handler for ``conda task export``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.exceptions import CondaSystemExit, DryRunExit
from conda.reporters import confirm_yn
from rich.console import Console

from ...parsers import detect_and_parse_tasks
from ...parsers.toml import tasks_to_toml

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


def execute_export(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Execute the ``conda task export`` subcommand."""
    if console is None:
        console = Console(highlight=False)
    file_path = getattr(args, "file", None)
    task_file, tasks = detect_and_parse_tasks(file_path=file_path)

    quiet = getattr(args, "quiet", False)
    output: Path | None = getattr(args, "output", None)

    text = tasks_to_toml(tasks)

    if output is not None:
        try:
            if output.is_file():
                confirm_yn(f"Overwrite {output}?")
        except (CondaSystemExit, DryRunExit):
            return 0

        output.write_text(text, encoding="utf-8")
        n = len(tasks)
        if not quiet:
            noun = "task" if n == 1 else "tasks"
            console.print(
                f"[bold cyan]Exported[/bold cyan] {n} {noun}"
                f" to [bold]{output}[/bold]"
            )
    else:
        print(text, end="")

    return 0
