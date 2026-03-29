"""CLI for ``conda workspace`` -- argparse configuration and dispatch."""

from __future__ import annotations

import argparse
from pathlib import Path

from conda.cli.helpers import (
    add_output_and_prompt_options,
    add_parser_help,
)


def generate_parser() -> argparse.ArgumentParser:
    """Build and return the parser -- used by sphinxarg.ext for docs."""
    parser = argparse.ArgumentParser(
        prog="conda workspace",
        description="Manage project-scoped multi-environment workspaces.",
        add_help=False,
    )
    configure_parser(parser)
    return parser


def configure_parser(parser: argparse.ArgumentParser) -> None:
    """Set up ``conda workspace`` CLI with subcommands."""
    add_parser_help(parser)

    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=None,
        help="Path to a specific workspace manifest instead of auto-detection.",
    )

    sub = parser.add_subparsers(dest="subcmd")

    init_parser = sub.add_parser(
        "init",
        help="Initialize a new workspace manifest.",
        add_help=False,
    )
    add_parser_help(init_parser)
    init_parser.add_argument(
        "--format",
        choices=["pixi", "conda", "pyproject"],
        default="conda",
        dest="manifest_format",
        help="Manifest format to generate (default: conda).",
    )
    init_parser.add_argument(
        "--name",
        default=None,
        help="Workspace name (defaults to directory name).",
    )
    init_parser.add_argument(
        "--channel",
        "-c",
        action="append",
        default=None,
        dest="channels",
        help="Channels to include (repeatable, default: conda-forge).",
    )
    init_parser.add_argument(
        "--platform",
        action="append",
        default=None,
        dest="platforms",
        help="Platforms to support (repeatable, auto-detected if omitted).",
    )

    install_parser = sub.add_parser(
        "install",
        help="Install (create/update) workspace environments.",
        add_help=False,
    )
    add_parser_help(install_parser)
    add_output_and_prompt_options(install_parser)
    install_parser.add_argument(
        "-e",
        "--environment",
        default=None,
        help="Install only this environment (default: all).",
    )
    install_parser.add_argument(
        "--force-reinstall",
        action="store_true",
        default=False,
        help="Remove and recreate environments from scratch.",
    )
    install_parser.add_argument(
        "--locked",
        action="store_true",
        default=False,
        help="Install from lockfiles, verifying they are up-to-date.",
    )
    install_parser.add_argument(
        "--frozen",
        action="store_true",
        default=False,
        help="Install from existing lockfiles without checking freshness.",
    )

    lock_parser = sub.add_parser(
        "lock",
        help="Solve and generate lockfiles for workspace environments.",
        add_help=False,
    )
    add_parser_help(lock_parser)
    add_output_and_prompt_options(lock_parser)
    lock_parser.add_argument(
        "-e",
        "--environment",
        default=None,
        help="Generate lockfile for this environment only (default: all).",
    )

    list_parser = sub.add_parser(
        "list",
        help="List packages in an environment, or list environments.",
        add_help=False,
    )
    add_parser_help(list_parser)
    add_output_and_prompt_options(list_parser)
    list_parser.add_argument(
        "-e",
        "--environment",
        default="default",
        help="Environment to list packages for (default: default).",
    )
    list_parser.add_argument(
        "--envs",
        action="store_true",
        default=False,
        help="List environments instead of packages.",
    )
    list_parser.add_argument(
        "--installed",
        action="store_true",
        default=False,
        help="Only show installed environments (with --envs).",
    )

    info_parser = sub.add_parser(
        "info",
        help="Show workspace overview or environment details.",
        add_help=False,
    )
    add_parser_help(info_parser)
    add_output_and_prompt_options(info_parser)
    info_parser.add_argument(
        "-e",
        "--environment",
        default=None,
        help="Show details for this environment (default: workspace overview).",
    )

    add_parser_cmd = sub.add_parser(
        "add",
        help="Add a dependency to the workspace.",
        add_help=False,
    )
    add_parser_help(add_parser_cmd)
    add_output_and_prompt_options(add_parser_cmd)
    add_parser_cmd.add_argument(
        "specs",
        nargs="+",
        help="Package specs to add (e.g. 'numpy >=1.24').",
    )
    add_parser_cmd.add_argument(
        "-e",
        "--environment",
        default=None,
        help="Target environment (adds to its feature instead of default).",
    )
    add_parser_cmd.add_argument(
        "--feature",
        default=None,
        help="Target feature directly (overrides --environment).",
    )
    add_parser_cmd.add_argument(
        "--pypi",
        action="store_true",
        default=False,
        help="Add as a PyPI dependency instead of conda.",
    )

    rm_parser = sub.add_parser(
        "remove",
        help="Remove a dependency from the workspace.",
        add_help=False,
    )
    add_parser_help(rm_parser)
    add_output_and_prompt_options(rm_parser)
    rm_parser.add_argument(
        "specs",
        nargs="+",
        help="Package names to remove.",
    )
    rm_parser.add_argument(
        "-e",
        "--environment",
        default=None,
        help="Target environment.",
    )
    rm_parser.add_argument(
        "--feature",
        default=None,
        help="Target feature directly.",
    )
    rm_parser.add_argument(
        "--pypi",
        action="store_true",
        default=False,
        help="Remove a PyPI dependency.",
    )

    clean_parser = sub.add_parser(
        "clean",
        help="Remove installed workspace environments.",
        add_help=False,
    )
    add_parser_help(clean_parser)
    add_output_and_prompt_options(clean_parser)
    clean_parser.add_argument(
        "-e",
        "--environment",
        default=None,
        help="Remove only this environment (default: all).",
    )

    run_parser = sub.add_parser(
        "run",
        help="Run a command in a workspace environment.",
        add_help=False,
    )
    add_parser_help(run_parser)
    run_parser.add_argument(
        "-e",
        "--environment",
        default="default",
        help="Environment to run in (default: default).",
    )
    run_parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command to execute in the environment.",
    )

    activate_parser = sub.add_parser(
        "activate",
        help="Print activation instructions for an environment.",
        add_help=False,
    )
    add_parser_help(activate_parser)
    activate_parser.add_argument(
        "-e",
        "--environment",
        default="default",
        help="Environment name (default: default).",
    )

    shell_parser = sub.add_parser(
        "shell",
        help="Spawn a new shell with an environment activated.",
        add_help=False,
    )
    add_parser_help(shell_parser)
    shell_parser.add_argument(
        "-e",
        "--environment",
        default="default",
        help="Environment name (default: default).",
    )
    shell_parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Optional command to run in the spawned shell.",
    )


def execute(args: argparse.Namespace) -> int:
    """Main entry point dispatched by the conda plugin system."""
    subcmd = args.subcmd

    if subcmd is None:
        generate_parser().print_help()
        return 0

    if subcmd == "init":
        from .init import execute_init

        return execute_init(args)
    elif subcmd == "install":
        from .install import execute_install

        return execute_install(args)
    elif subcmd == "lock":
        from .lock import execute_lock

        return execute_lock(args)
    elif subcmd == "list":
        from .list import execute_list

        return execute_list(args)
    elif subcmd == "info":
        from .info import execute_info

        return execute_info(args)
    elif subcmd == "add":
        from .add import execute_add

        return execute_add(args)
    elif subcmd == "remove":
        from .remove import execute_remove

        return execute_remove(args)
    elif subcmd == "clean":
        from .clean import execute_clean

        return execute_clean(args)
    elif subcmd == "run":
        from .run import execute_run

        return execute_run(args)
    elif subcmd == "activate":
        from .activate import execute_activate

        return execute_activate(args)
    elif subcmd == "shell":
        from .shell import execute_shell

        return execute_shell(args)
    else:
        generate_parser().print_help()
        return 0
