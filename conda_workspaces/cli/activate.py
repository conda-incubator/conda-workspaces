"""``conda workspace activate`` — print activation instructions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.cli.common import print_activate

from ..context import WorkspaceContext
from ..exceptions import EnvironmentNotFoundError, EnvironmentNotInstalledError
from ..parsers import detect_and_parse

if TYPE_CHECKING:
    import argparse


def execute_activate(args: argparse.Namespace) -> int:
    """Print activation instructions for a workspace environment."""
    manifest_path = getattr(args, "file", None)
    _, config = detect_and_parse(manifest_path)
    ctx = WorkspaceContext(config)

    env_name = args.environment

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)

    prefix = ctx.env_prefix(env_name)
    print_activate(str(prefix))
    return 0
