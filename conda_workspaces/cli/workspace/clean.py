"""``conda workspace clean`` — remove installed workspace environments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.exceptions import CondaSystemExit, DryRunExit
from conda.reporters import confirm_yn
from rich.console import Console

from ...envs import clean_all, list_installed_environments, remove_environment
from ...exceptions import EnvironmentNotFoundError
from .. import status
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse


def execute_clean(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Remove installed workspace environments."""
    if console is None:
        console = Console(highlight=False)
    config, ctx = workspace_context_from_args(args)

    env_name = getattr(args, "environment", None)

    try:
        if env_name:
            if env_name not in config.environments:
                raise EnvironmentNotFoundError(
                    env_name, list(config.environments.keys())
                )

            if not ctx.env_exists(env_name):
                console.print(
                    f"[bold]{env_name}[/bold] environment is not installed."
                    " Run 'conda workspace install"
                    f" -e {env_name}' to create it."
                )
                return 0

            confirm_yn(f"Remove {env_name} environment?")

            remove_environment(ctx, env_name)
            status.message(console, "Removed", "environment", env_name)
        else:
            installed = list_installed_environments(ctx)
            if not installed:
                console.print(
                    "No environments installed."
                    " Run 'conda workspace install' to create them."
                )
                return 0

            if not conda_context.always_yes:
                names = ", ".join(installed)
                confirm_yn(f"Remove {names} environments?")

            for i, name in enumerate(installed):
                if i > 0:
                    console.print()
                status.message(
                    console, "Removing", "environment", name,
                    style="bold blue", ellipsis=True,
                )
            clean_all(ctx)
            for i, name in enumerate(installed):
                if i > 0:
                    console.print()
                status.message(console, "Removed", "environment", name)
    except (CondaSystemExit, DryRunExit):
        return 0

    return 0
