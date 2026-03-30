"""Handler for ``conda task list``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from ...parsers import detect_and_parse_tasks

if TYPE_CHECKING:
    import argparse


def execute_list(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Execute the ``conda task list`` subcommand."""
    file_path = getattr(args, "file", None)
    task_file, tasks = detect_and_parse_tasks(file_path=file_path)

    if console is None:
        console = Console(highlight=False)

    use_json = getattr(args, "json", False)

    visible_tasks = {name: t for name, t in sorted(tasks.items()) if not t.is_hidden}

    if use_json:
        data: dict[str, dict[str, object]] = {}
        for name, task in visible_tasks.items():
            entry: dict[str, object] = {"name": name}
            if task.cmd is not None:
                entry["cmd"] = task.cmd
            if task.description:
                entry["description"] = task.description
            if task.depends_on:
                entry["depends_on"] = [d.task for d in task.depends_on]
            if task.is_alias:
                entry["alias"] = True
            data[name] = entry

        console.print_json(json.dumps({"tasks": data, "file": str(task_file)}))
        return 0

    if not visible_tasks:
        console.print(
            f"No tasks defined in {task_file}."
            " Add tasks with 'conda task add'."
        )
        return 0

    console.print(f"[dim]{task_file}[/dim]")

    has_cmds = any(t.cmd for t in visible_tasks.values())
    has_deps = any(t.depends_on for t in visible_tasks.values())

    table = Table(show_edge=False, pad_edge=False, show_header=False, box=None)
    table.add_column(style="bold")
    if has_cmds:
        table.add_column(style="dim")
    if has_deps:
        table.add_column(style="dim cyan")

    for name, task in visible_tasks.items():
        cmd_col = ""
        if task.is_alias:
            cmd_col = "(alias)"
        elif task.cmd is not None:
            cmd = task.cmd if isinstance(task.cmd, str) else " ".join(task.cmd)
            cmd_col = task.description or cmd

        deps_col = ""
        if task.depends_on:
            dep_names = ", ".join(d.task for d in task.depends_on)
            deps_col = f"← {dep_names}"

        row: list[str] = [name]
        if has_cmds:
            row.append(cmd_col)
        if has_deps:
            row.append(deps_col)
        table.add_row(*row)

    console.print(table)

    return 0
