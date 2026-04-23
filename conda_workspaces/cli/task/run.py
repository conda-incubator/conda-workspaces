"""Handler for ``conda task run``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context
from rich.console import Console
from rich.tree import Tree

from ...cache import is_cached, save_cache
from ...exceptions import CondaWorkspacesError, TaskExecutionError
from ...graph import resolve_execution_order
from ...manifests import detect_and_parse_tasks
from ...runner import SubprocessShell
from ...template import render, render_list
from .. import status

if TYPE_CHECKING:
    import argparse

    from ...models import Task


def _env_prefix_or_none(
    args: argparse.Namespace,
    env_name: str | None = None,
) -> Path | None:
    """Resolve an environment name to its prefix, or return ``None``.

    Falls back to ``None`` when no workspace exists or the environment
    is not defined — so tasks can run in the current shell.
    """
    if env_name is None:
        env_name = getattr(args, "environment", None)
    if not env_name:
        return None
    try:
        from ...context import WorkspaceContext
        from ...exceptions import CondaWorkspacesError
        from ...manifests import detect_and_parse

        manifest_path = getattr(args, "file", None)
        _, config = detect_and_parse(manifest_path)
        ctx = WorkspaceContext(config)
        if env_name in config.environments:
            return ctx.env_prefix(env_name)
    except CondaWorkspacesError:
        pass
    return None


def _resolve_task_args(task: Task, cli_args: list[str]) -> dict[str, str]:
    """Map positional CLI arguments to named task arguments."""
    result: dict[str, str] = {}
    for i, task_arg in enumerate(task.args):
        if i < len(cli_args):
            value = cli_args[i]
            if task_arg.choices and value not in task_arg.choices:
                raise CondaWorkspacesError(
                    f"Invalid value '{value}' for argument '{task_arg.name}' "
                    f"in task '{task.name}'. "
                    f"Choices: {', '.join(task_arg.choices)}"
                )
            result[task_arg.name] = value
        elif task_arg.default is not None:
            result[task_arg.name] = task_arg.default
        else:
            raise CondaWorkspacesError(
                f"Missing required argument '{task_arg.name}' for task '{task.name}'"
            )
    return result


def _build_dry_run_tree(
    target_name: str,
    tasks: dict[str, Task],
    rendered_cmds: dict[str, str],
) -> Tree:
    """Build a Rich Tree for dry-run display."""

    def _label(name: str) -> str:
        return status.message_label(
            "Would run",
            "task",
            name,
            detail=rendered_cmds.get(name),
        )

    def _add_children(parent: Tree, task_name: str, seen: set[str]) -> None:
        for dep in tasks[task_name].depends_on:
            if dep.task in seen:
                continue
            child = parent.add(_label(dep.task))
            _add_children(child, dep.task, seen | {dep.task})

    tree = Tree(_label(target_name))
    _add_children(tree, target_name, {target_name})
    return tree


def execute_run(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Execute the ``conda task run`` subcommand."""
    if console is None:
        console = Console(highlight=False)

    task_file, tasks = detect_and_parse_tasks(file_path=getattr(args, "file", None))
    project_root = task_file.parent

    subdir = context.subdir
    tasks = {name: t.resolve_for_platform(subdir) for name, t in tasks.items()}

    target_name = args.task_name
    skip_deps = getattr(args, "skip_deps", False)

    if target_name not in tasks:
        return _run_adhoc(args, target_name, task_file, console=console)

    order = resolve_execution_order(
        target_name,
        tasks,
        skip_deps=skip_deps,
    )

    dry_run = getattr(args, "dry_run", False)
    quiet = getattr(args, "quiet", False)
    verbose = getattr(args, "verbosity", 0) or 0
    user_env = getattr(args, "environment", None)
    conda_prefix = _env_prefix_or_none(args)

    default_env_name = tasks[target_name].default_environment
    if default_env_name and not user_env:
        conda_prefix = _env_prefix_or_none(args, default_env_name) or conda_prefix
    elif not user_env and conda_prefix is None:
        conda_prefix = _env_prefix_or_none(args, "default")

    task_args = _resolve_task_args(tasks[target_name], args.task_args)

    if dry_run:
        return _execute_dry_run(
            order,
            tasks,
            target_name,
            task_args,
            task_file,
            quiet=quiet,
            console=console,
        )

    has_deps = len(order) > 1 or tasks[target_name].is_alias
    shell = SubprocessShell()
    task_count = 0

    for name in order:
        task = tasks[name]

        if task.is_alias:
            continue

        if name == target_name:
            current_args = task_args
        else:
            dep_info = next(
                (d for d in tasks[target_name].depends_on if d.task == name),
                None,
            )
            current_args = {}
            if dep_info and dep_info.args:
                for i, da in enumerate(dep_info.args):
                    if isinstance(da, dict):
                        current_args.update(da)
                    elif i < len(task.args):
                        current_args[task.args[i].name] = render(
                            da,
                            manifest_path=task_file,
                            task_args=task_args,
                        )

            dep_prefix = conda_prefix
            if dep_info and dep_info.environment:
                dep_prefix = (
                    _env_prefix_or_none(args, dep_info.environment) or conda_prefix
                )

        cmd = task.cmd
        if cmd is None:
            continue
        if isinstance(cmd, list):
            cmd = " ".join(cmd)

        cmd = render(cmd, manifest_path=task_file, task_args=current_args)

        task_env = {
            k: render(v, manifest_path=task_file, task_args=current_args)
            for k, v in task.env.items()
        }

        cwd = Path(getattr(args, "cwd", None) or task.cwd or project_root)
        clean_env = getattr(args, "clean_env", False) or task.clean_env

        task_prefix = (
            dep_prefix
            if name != target_name and dep_info and dep_info.environment
            else conda_prefix
        )

        rendered_inputs = render_list(
            task.inputs, manifest_path=task_file, task_args=current_args
        )
        rendered_outputs = render_list(
            task.outputs, manifest_path=task_file, task_args=current_args
        )

        if rendered_inputs or rendered_outputs:
            if is_cached(
                project_root,
                name,
                cmd,
                task_env,
                rendered_inputs,
                rendered_outputs,
                cwd,
            ):
                if not quiet:
                    status.message(
                        console,
                        "Skipped",
                        "task",
                        name,
                        style="bold yellow",
                        suffix="cached",
                    )
                continue

        if has_deps and not quiet:
            if task_count > 0:
                console.print()
            status.message(
                console,
                "Running",
                "task",
                name,
                style="bold blue",
                ellipsis=True,
                detail=cmd if verbose else None,
            )
        task_count += 1

        if has_deps and verbose and (rendered_inputs or rendered_outputs):
            if rendered_inputs:
                console.print(f"  [dim]inputs: {rendered_inputs}[/dim]")
            if rendered_outputs:
                console.print(f"  [dim]outputs: {rendered_outputs}[/dim]")

        exit_code = shell.run(
            cmd,
            task_env,
            cwd,
            conda_prefix=task_prefix,
            clean_env=clean_env,
        )

        if exit_code != 0:
            if has_deps and not quiet:
                status.message(
                    console,
                    "Failed",
                    "task",
                    name,
                    style="bold yellow",
                )
            raise TaskExecutionError(name, exit_code)

        if has_deps and not quiet:
            status.message(console, "Finished", "task", name)

        if rendered_inputs or rendered_outputs:
            save_cache(
                project_root,
                name,
                cmd,
                task_env,
                rendered_inputs,
                rendered_outputs,
                cwd,
            )

    if has_deps and not quiet and tasks[target_name].is_alias:
        status.message(console, "Finished", "task", target_name)

    return 0


def _execute_dry_run(
    order: list[str],
    tasks: dict[str, Task],
    target_name: str,
    task_args: dict[str, str],
    task_file: Path,
    *,
    quiet: bool,
    console: Console,
) -> int:
    """Render commands and display a static Rich Tree for dry-run."""
    rendered_cmds: dict[str, str] = {}
    for name in order:
        task = tasks[name]
        if task.is_alias:
            continue

        if name == target_name:
            current_args = task_args
        else:
            dep_info = next(
                (d for d in tasks[target_name].depends_on if d.task == name),
                None,
            )
            current_args = {}
            if dep_info and dep_info.args:
                for i, da in enumerate(dep_info.args):
                    if isinstance(da, dict):
                        current_args.update(da)
                    elif i < len(task.args):
                        current_args[task.args[i].name] = render(
                            da,
                            manifest_path=task_file,
                            task_args=task_args,
                        )

        cmd = task.cmd
        if cmd is None:
            continue
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        rendered_cmds[name] = render(
            cmd, manifest_path=task_file, task_args=current_args
        )

    if not quiet:
        tree = _build_dry_run_tree(target_name, tasks, rendered_cmds)
        console.print(tree)
    return 0


def _run_adhoc(
    args: argparse.Namespace,
    cmd_name: str,
    task_file: Path,
    *,
    console: Console,
) -> int:
    """Run an ad-hoc shell command (not a named task)."""
    task_args = list(getattr(args, "task_args", []))
    full_cmd = " ".join([cmd_name, *task_args])

    templated = getattr(args, "templated", False)
    if templated:
        full_cmd = render(full_cmd, manifest_path=task_file)

    dry_run = getattr(args, "dry_run", False)
    conda_prefix = _env_prefix_or_none(args)
    if conda_prefix is None and not getattr(args, "environment", None):
        conda_prefix = _env_prefix_or_none(args, "default")

    if dry_run:
        if not getattr(args, "quiet", False):
            console.print(f"[bold yellow]Would run[/bold yellow] [dim]{full_cmd}[/dim]")
        return 0

    shell = SubprocessShell()
    cwd = Path(getattr(args, "cwd", None) or task_file.parent)
    exit_code = shell.run(
        full_cmd,
        {},
        cwd,
        conda_prefix=conda_prefix,
    )

    if exit_code != 0:
        raise TaskExecutionError(cmd_name, exit_code)

    return 0
