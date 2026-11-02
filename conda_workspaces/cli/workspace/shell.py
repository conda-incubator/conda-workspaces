"""``conda workspace shell`` — spawn a shell in a workspace environment."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from conda.common.compat import on_win

from ...exceptions import (
    CondaWorkspacesError,
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse


def execute_shell(args: argparse.Namespace) -> int:
    """Spawn a new shell with a workspace environment activated.

    Delegates to ``conda-spawn`` which handles shell detection, activation
    scripts, prompt modification, and process lifecycle.  The user exits
    the spawned shell with ``exit`` or Ctrl+D to return to the parent
    session — no ``deactivate`` required.
    """
    from conda_spawn.main import detect_shell_class, spawn

    config, ctx = workspace_context_from_args(args)

    env_name = getattr(args, "environment", "default")

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)

    prefix = ctx.env_prefix(env_name)
    # Remove this restriction when supported conda activators escape prefix paths.
    if any(
        char in "\"'`$%!^&|;()<>" or ord(char) < 32 or 0x7F <= ord(char) <= 0x9F
        for char in str(prefix)
    ):
        raise CondaWorkspacesError(
            "Cannot safely activate a workspace path containing shell metacharacters.",
            hints=[
                "Use a workspace path without quotes, shell expansion characters,"
                " or control characters."
            ],
        )

    shell_cls = detect_shell_class()
    shell_name = shell_cls(prefix).executable()
    workspace_root = Path(config.root).resolve()
    requested_executable = Path(shell_name)
    if requested_executable.is_absolute():
        candidates = [requested_executable]
    elif requested_executable.name == shell_name:
        if on_win and not requested_executable.suffix:
            shell_name += ".exe"
        candidates = [
            Path(directory) / shell_name
            for directory in os.get_exec_path()
            if Path(directory).is_absolute()
        ]
    else:
        candidates = []
    executable = None
    for candidate in candidates:
        # Absolute candidates avoid Windows' implicit current-directory search.
        found = shutil.which(str(candidate))
        if found is not None:
            resolved = Path(found).resolve()
            if not resolved.is_relative_to(workspace_root):
                executable = found
                break
    if executable is None:
        raise CondaWorkspacesError(
            "Cannot find a shell executable outside the workspace.",
            hints=["Install a supported shell in an absolute directory on PATH."],
        )

    class ResolvedShell(shell_cls):  # ty: ignore[unsupported-base]
        """Keep conda-spawn from searching the restored workspace for a shell."""

        def executable(self) -> str:
            return executable

    # Collect optional command after ``--``
    command = getattr(args, "cmd", None) or None
    if command and command[0] == "--":
        command = command[1:]
    if command is not None and len(command) == 0:
        command = None

    cwd = getattr(args, "cwd", None)
    original_cwd = Path.cwd()
    try:
        if cwd is not None:
            os.chdir(cwd)
        # conda-spawn accepts a class but annotates shell_cls as an instance.
        return spawn(prefix=prefix, shell_cls=ResolvedShell, command=command)  # ty: ignore[invalid-argument-type]
    finally:
        if cwd is not None:
            os.chdir(original_cwd)
