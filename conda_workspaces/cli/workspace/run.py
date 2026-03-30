"""``conda workspace run`` — run a command in a workspace environment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...exceptions import (
    CondaWorkspacesError,
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse


def execute_run(args: argparse.Namespace) -> int:
    """Run a one-shot command in a workspace environment.

    Delegates to ``conda.cli.main_run.execute`` after pointing
    ``context.target_prefix`` at the resolved workspace environment.
    No interactive shell is spawned.
    """
    import argparse as _argparse

    from conda.base.context import context
    from conda.cli.main_run import execute as conda_run

    config, ctx = workspace_context_from_args(args)

    env_name = getattr(args, "environment", "default")

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)

    prefix = str(ctx.env_prefix(env_name))

    cmd = getattr(args, "cmd", None) or []
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        raise CondaWorkspacesError(
            "No command specified.",
            hints=["Usage: conda workspace run -e ENV -- COMMAND [ARGS...]"],
        )

    context.__init__(argparse_args=_argparse.Namespace(prefix=prefix))

    run_args = _argparse.Namespace(
        executable_call=cmd,
        dev=False,
        debug_wrapper_scripts=False,
        cwd=None,
        no_capture_output=True,
    )
    return conda_run(run_args, None)
