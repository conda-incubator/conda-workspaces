"""``conda workspace clean`` — remove installed workspace environments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.exceptions import CondaSystemExit, DryRunExit
from conda.reporters import confirm_yn

from ..context import WorkspaceContext
from ..envs import clean_all, list_installed_environments, remove_environment
from ..exceptions import EnvironmentNotFoundError
from ..parsers import detect_and_parse

if TYPE_CHECKING:
    import argparse


def execute_clean(args: argparse.Namespace) -> int:
    """Remove installed workspace environments."""
    manifest_path = getattr(args, "file", None)
    _, config = detect_and_parse(manifest_path)
    ctx = WorkspaceContext(config)

    env_name = getattr(args, "environment", None)

    try:
        if env_name:
            if env_name not in config.environments:
                raise EnvironmentNotFoundError(
                    env_name, list(config.environments.keys())
                )

            if not ctx.env_exists(env_name):
                print(f"Environment '{env_name}' is not installed.")
                return 0

            confirm_yn(f"Remove environment '{env_name}'?")

            remove_environment(ctx, env_name)
            print(f"Removed environment '{env_name}'.")
        else:
            installed = list_installed_environments(ctx)
            if not installed:
                print("No environments installed.")
                return 0

            print(f"This will remove {len(installed)} environment(s):")
            for name in installed:
                print(f"  - {name}")
            confirm_yn("Continue?")

            clean_all(ctx)
            print(f"Removed {len(installed)} environment(s).")
    except (CondaSystemExit, DryRunExit):
        return 0

    return 0
