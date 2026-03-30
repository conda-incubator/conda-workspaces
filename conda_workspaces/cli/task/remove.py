"""Handler for ``conda task remove``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from ...parsers import detect_and_parse_tasks, find_parser

if TYPE_CHECKING:
    import argparse


def execute_remove(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Execute the ``conda task remove`` subcommand."""
    if console is None:
        console = Console(highlight=False)
    file_path = getattr(args, "file", None)
    task_file, _ = detect_and_parse_tasks(file_path=file_path)

    parser = find_parser(task_file)

    dry_run = getattr(args, "dry_run", False)
    quiet = getattr(args, "quiet", False)

    if dry_run:
        console.print(
            "[bold yellow]Would remove[/bold yellow]"
            f" [bold]{args.task_name}[/bold] task"
            f" from [dim]{task_file}[/dim]"
        )
        return 0

    parser.remove_task(task_file, args.task_name)
    if not quiet:
        console.print(
            "[bold cyan]Removed[/bold cyan]"
            f" [bold]{args.task_name}[/bold] task"
            f" from [dim]{task_file}[/dim]"
        )
    return 0
