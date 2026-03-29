"""``conda workspace shell`` — spawn a shell in a workspace environment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..context import WorkspaceContext
from ..exceptions import EnvironmentNotFoundError, EnvironmentNotInstalledError
from ..parsers import detect_and_parse

if TYPE_CHECKING:
    import argparse


def execute_shell(args: argparse.Namespace) -> int:
    """Spawn a new shell with a workspace environment activated.

    Delegates to ``conda-spawn`` which handles shell detection, activation
    scripts, prompt modification, and process lifecycle.  The user exits
    the spawned shell with ``exit`` or Ctrl+D to return to the parent
    session — no ``deactivate`` required.
    """
    from conda_spawn.main import spawn

    manifest_path = getattr(args, "file", None)
    _, config = detect_and_parse(manifest_path)
    ctx = WorkspaceContext(config)

    env_name = getattr(args, "environment", "default")

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)

    prefix = ctx.env_prefix(env_name)

    # Collect optional command after ``--``
    command = getattr(args, "cmd", None) or None
    if command and command[0] == "--":
        command = command[1:]
    if command is not None and len(command) == 0:
        command = None

    return spawn(prefix=prefix, command=command)
