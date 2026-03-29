"""``conda workspace run`` — run tasks or commands in a workspace environment.

When conda-tasks is installed, the first argument is resolved as a task
name.  If no matching task is found (or conda-tasks is not available),
the arguments are executed as an arbitrary shell command inside the
activated workspace environment — mirroring ``pixi run``'s dual-purpose
behaviour.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.common.compat import encode_environment
from conda.exceptions import ArgumentError
from conda.gateways.disk.delete import rm_rf
from conda.gateways.subprocess import subprocess_call
from conda.utils import wrap_subprocess_call

from ..context import WorkspaceContext
from ..exceptions import EnvironmentNotFoundError, EnvironmentNotInstalledError
from ..parsers import detect_and_parse

if TYPE_CHECKING:
    import argparse

log = logging.getLogger(__name__)


def _try_run_task(args: argparse.Namespace, task_name: str) -> int | None:
    """Attempt to run *task_name* via conda-tasks.

    Returns the exit code on success, or ``None`` when conda-tasks is
    not installed or *task_name* is not a known task.
    """
    try:
        from conda_tasks.parsers import detect_and_parse as detect_tasks
    except ImportError:
        return None

    try:
        _, tasks = detect_tasks()
    except Exception:
        return None

    if task_name not in tasks:
        return None

    log.info("Resolved '%s' as a conda-tasks task", task_name)

    from conda_tasks.cli.run import execute_run as tasks_execute_run

    task_ns = _build_task_namespace(args, task_name)
    return tasks_execute_run(task_ns)


def _build_task_namespace(
    args: argparse.Namespace, task_name: str
) -> argparse.Namespace:
    """Build an argparse.Namespace compatible with conda-tasks' execute_run."""
    import argparse as _argparse

    cmd = list(getattr(args, "cmd", []))
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    # Everything after the task name becomes task arguments
    task_args = cmd[1:] if len(cmd) > 1 else []

    return _argparse.Namespace(
        task_name=task_name,
        task_args=task_args,
        file=getattr(args, "file", None),
        name=None,
        prefix=None,
        skip_deps=False,
        dry_run=False,
        quiet=False,
        verbose=0,
        clean_env=False,
        cwd=None,
    )


def _run_command(args: argparse.Namespace) -> int:
    """Execute an arbitrary command in a workspace environment."""
    manifest_path = getattr(args, "file", None)
    _, config = detect_and_parse(manifest_path)
    ctx = WorkspaceContext(config)

    env_name = getattr(args, "environment", "default")
    cmd = list(getattr(args, "cmd", []))

    if not cmd:
        raise ArgumentError("No command specified. Please provide a command to run.")

    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)

    prefix = ctx.env_prefix(env_name)

    script, command = wrap_subprocess_call(
        conda_context.root_prefix,
        str(prefix),
        False,
        False,
        cmd,
    )

    response = subprocess_call(
        command,
        env=encode_environment(os.environ.copy()),
        path=str(ctx.root),
        raise_on_error=False,
        capture_output=False,
    )

    if "CONDA_TEST_SAVE_TEMPS" not in os.environ:
        rm_rf(script)

    return response.rc


def execute_run(args: argparse.Namespace) -> int:
    """Run a task or command in a workspace environment."""
    cmd = list(getattr(args, "cmd", []))

    # Strip leading -- to find the actual first token
    tokens = cmd[1:] if cmd and cmd[0] == "--" else cmd

    if tokens:
        task_result = _try_run_task(args, tokens[0])
        if task_result is not None:
            return task_result

    return _run_command(args)
