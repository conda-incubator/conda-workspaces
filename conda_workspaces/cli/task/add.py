"""Handler for ``conda task add``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ...exceptions import WorkspaceParseError
from ...manifests import detect_task_file, find_parser
from ...manifests.toml import CondaTomlParser
from ...models import Task, TaskDependency
from ...paths import validate_path_parent

if TYPE_CHECKING:
    import argparse


def execute_add(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Execute the ``conda task add`` subcommand."""
    if console is None:
        console = Console(highlight=False)
    file_path = getattr(args, "file", None)
    if file_path is None:
        file_path = detect_task_file(reject_symlinks=True)
        if file_path is None:
            file_path = Path.cwd() / "conda.toml"
    elif file_path.is_symlink():
        raise WorkspaceParseError(
            file_path,
            "symbolic links are not supported for this operation",
        )
    else:
        file_path = file_path.absolute()
        validate_path_parent(file_path)

    try:
        parser = find_parser(file_path)
    except Exception:
        if file_path.exists():
            raise
        parser = CondaTomlParser()

    depends = [TaskDependency(task=d) for d in (args.depends_on or [])]
    task = Task(
        name=args.task_name,
        cmd=args.cmd,
        depends_on=depends,
        description=args.description,
    )

    dry_run = getattr(args, "dry_run", False)
    quiet = getattr(args, "quiet", False)

    if dry_run:
        console.print(
            "[bold yellow]Would add[/bold yellow]"
            f" [bold]{args.task_name}[/bold] task"
            f" to [dim]{file_path}[/dim]"
        )
        return 0

    parser.add_task(file_path, args.task_name, task)
    if not quiet:
        console.print(
            "[bold cyan]Added[/bold cyan]"
            f" [bold]{args.task_name}[/bold] task"
            f" to [dim]{file_path}[/dim]"
        )
    return 0
