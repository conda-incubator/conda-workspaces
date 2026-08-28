"""``conda workspace clean`` — remove installed workspace environments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.exceptions import CondaSystemExit, DryRunExit
from conda.reporters import confirm_yn
from rich.console import Console

from ...envs import remove_environment
from ...exceptions import CondaWorkspacesError, EnvironmentNotFoundError
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
    dry_run = getattr(args, "dry_run", False)

    try:
        if env_name:
            declared = env_name in config.environments
            envs_identity = ctx.envs_dir_identity()
            prefix_identity = next(
                (
                    identity
                    for prefix, identity in ctx.iter_installed_prefixes()
                    if prefix.name == env_name
                ),
                None,
            )
            if ctx.envs_dir_identity() != envs_identity:
                raise CondaWorkspacesError(
                    "Workspace environments directory changed while it was inspected."
                )
            if prefix_identity is None:
                if not declared:
                    raise EnvironmentNotFoundError(
                        env_name, list(config.environments.keys())
                    )
                console.print(
                    f"[bold]{status.escape_for_console(env_name)}[/bold]"
                    " environment is not installed."
                    " Run 'conda workspace install"
                    f" -e {status.escape_for_console(env_name)}' to create it."
                )
                return 0
            if envs_identity is None:
                raise CondaWorkspacesError(
                    "Workspace environments directory changed while it was inspected."
                )

            installed = [env_name]
            if not dry_run:
                confirm_yn(f"Remove {status.escape_for_console(env_name)} environment?")
                remove_environment(
                    ctx,
                    env_name,
                    expected_envs_identity=envs_identity,
                    expected_prefix_identity=prefix_identity,
                )
        else:
            envs_identity = ctx.envs_dir_identity()
            prefixes = sorted(
                ctx.iter_installed_prefixes(),
                key=lambda item: item[0].name,
            )
            if ctx.envs_dir_identity() != envs_identity:
                raise CondaWorkspacesError(
                    "Workspace environments directory changed while it was inspected."
                )
            if not prefixes:
                console.print(
                    "No environments installed."
                    " Run 'conda workspace install' to create them."
                )
                return 0
            if envs_identity is None:
                raise CondaWorkspacesError(
                    "Workspace environments directory changed while it was inspected."
                )
            installed = [prefix.name for prefix, _ in prefixes]

            if not dry_run:
                if not conda_context.always_yes:
                    names = ", ".join(
                        status.escape_for_console(name) for name in installed
                    )
                    confirm_yn(f"Remove {names} environments?")

                for i, name in enumerate(installed):
                    if i > 0:
                        console.print()
                    status.message(
                        console,
                        "Removing",
                        "environment",
                        name,
                        style="bold blue",
                        ellipsis=True,
                    )
                for prefix, prefix_identity in prefixes:
                    remove_environment(
                        ctx,
                        prefix.name,
                        expected_envs_identity=envs_identity,
                        expected_prefix_identity=prefix_identity,
                    )

        for i, name in enumerate(installed):
            if i > 0:
                console.print()
            status.message(
                console,
                "Would remove" if dry_run else "Removed",
                "environment",
                name,
            )
    except (CondaSystemExit, DryRunExit):
        return 0

    return 0
