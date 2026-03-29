"""``conda workspace run`` — run a command in a workspace environment."""

from __future__ import annotations

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


def execute_run(args: argparse.Namespace) -> int:
    """Run a command in a workspace environment."""
    manifest_path = getattr(args, "file", None)
    _, config = detect_and_parse(manifest_path)
    ctx = WorkspaceContext(config)

    env_name = getattr(args, "environment", "default")
    cmd = getattr(args, "cmd", [])

    if not cmd:
        raise ArgumentError("No command specified. Please provide a command to run.")

    # Strip leading -- if present (from argparse.REMAINDER)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)

    prefix = ctx.env_prefix(env_name)

    # Use conda's internal activation and subprocess machinery
    script, command = wrap_subprocess_call(
        conda_context.root_prefix,
        str(prefix),
        False,  # dev mode
        False,  # debug_wrapper_scripts
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
