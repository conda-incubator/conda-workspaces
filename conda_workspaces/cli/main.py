"""CLI for ``conda workspace`` and ``conda task``.

Argparse configuration and dispatch for both subcommands.
"""

from __future__ import annotations

import argparse
from argparse import SUPPRESS
from pathlib import Path

from conda.base.context import context as conda_context
from conda.cli.helpers import (
    LazyChoicesAction,
    add_output_and_prompt_options,
    add_parser_help,
)
from conda.common.constants import NULL
from conda.exceptions import CondaError, CondaSystemExit, DryRunExit


def _accept_json_silently(parser: argparse.ArgumentParser) -> None:
    """Accept ``--json`` on a side-effect subcommand without advertising it.

    Mirrors conda's own pre-parser trick (``conda/cli/conda_argparse.py``
    registers ``--json`` with ``help=SUPPRESS`` on the pre-parser, so
    every top-level conda command tolerates the flag). Our subcommands
    that do not have structured output to emit — ``init``, ``activate``,
    ``run``, ``shell`` — still get piped by scripts and CI wrappers that
    set ``--json`` globally; crashing with ``unrecognized arguments:
    --json`` is the wrong UX. This helper lets those commands silently
    accept the flag, produce no output on ``--json`` (so the caller's
    JSON parser still sees a clean stream on stdout), and rely on the
    exit code for status.

    Only register ``--json`` via this helper on commands that do **not**
    call :func:`add_output_and_prompt_options`; otherwise argparse
    raises ``ArgumentError: conflicting option string``.
    """
    parser.add_argument(
        "--json",
        action="store_true",
        default=NULL,
        help=SUPPRESS,
    )


def _handle_error(exc: CondaError) -> int:
    """Render a CondaError with Rich and return its exit code.

    In JSON or debug mode, re-raises so conda's own handler takes over
    (preserving tracebacks and structured JSON output).
    """
    if conda_context.json or conda_context.debug:
        raise exc

    from rich.console import Console

    from . import status

    console = Console(stderr=True, highlight=False)
    status.print_error(console, exc)
    return getattr(exc, "return_code", 1)


def generate_workspace_parser() -> argparse.ArgumentParser:
    """Build and return the workspace parser — used by sphinxarg.ext for docs."""
    parser = argparse.ArgumentParser(
        prog="conda workspace",
        description="Manage project-scoped multi-environment workspaces.",
        add_help=False,
    )
    configure_workspace_parser(parser)
    return parser


def configure_workspace_parser(parser: argparse.ArgumentParser) -> None:
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
    _accept_json_silently(init_parser)
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
    lock_parser.add_argument(
        "--platform",
        action="append",
        default=None,
        help=(
            "Lock only for this platform (e.g. linux-64). May be passed"
            " multiple times. Defaults to all platforms declared in the"
            " workspace."
        ),
    )
    lock_parser.add_argument(
        "--skip-unsolvable",
        action="store_true",
        default=False,
        help=(
            "Continue locking when the solver fails for an (environment,"
            " platform) pair instead of aborting. Other errors (missing"
            " channel, invalid manifest, etc.) still abort. Fails if no"
            " pair can be solved."
        ),
    )
    lock_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Write the lockfile to this path instead of the default"
            " <workspace>/conda.lock. Useful in CI matrices that emit"
            " per-platform fragments (e.g. --platform linux-64 --output"
            " conda.lock.linux-64) to be stitched back with --merge."
        ),
    )
    lock_parser.add_argument(
        "--merge",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Merge pre-existing conda.lock fragments into a single"
            " <workspace>/conda.lock without solving. May be passed"
            " multiple times; each value is treated as a glob"
            " (e.g. --merge 'conda.lock.*'). Cannot be combined with"
            " --environment, --platform, --skip-unsolvable, or --output."
        ),
    )

    export_parser = sub.add_parser(
        "export",
        help="Export a workspace environment to environment.yml / conda.lock / ...",
        add_help=False,
    )
    add_parser_help(export_parser)
    add_output_and_prompt_options(export_parser)

    export_parser.add_argument(
        "-e",
        "--environment",
        default="default",
        help="Environment to export (default: default).",
    )

    # Format choices are resolved lazily via
    # context.plugin_manager.get_exporter_format_mapping() so any
    # exporter plugin (conda-workspaces' own conda.lock, conda's
    # built-in environment-yaml / environment-json, third-party
    # rattler-lock, ...) appears the moment it is installed.
    def _format_choices() -> list[str]:
        return sorted(conda_context.plugin_manager.get_exporter_format_mapping().keys())

    export_parser.add_argument(
        "--format",
        default=None,
        action=LazyChoicesAction,
        choices_func=_format_choices,
        help=(
            "Export format name or alias (e.g. environment-yaml, json, "
            "conda-workspaces-lock-v1). Defaults to environment-yaml when "
            "--file is omitted, or detected from --file's basename."
        ),
    )
    export_parser.add_argument(
        "-f",
        "--file",
        type=Path,
        default=None,
        help="Write output to this file (default: stdout).",
    )
    export_parser.add_argument(
        "--platform",
        action="append",
        default=None,
        dest="export_platforms",
        help=(
            "Restrict export to this platform (e.g. linux-64). Repeatable."
            " When multiple platforms are given, the chosen format must"
            " support multi-platform export (e.g. conda-workspaces-lock-v1)."
        ),
    )

    source_group = export_parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--from-lockfile",
        action="store_true",
        default=False,
        help=(
            "Build the export from an existing conda.lock via the"
            " CondaLockLoader, instead of the declared manifest."
        ),
    )
    source_group.add_argument(
        "--from-prefix",
        action="store_true",
        default=False,
        help=(
            "Build the export from the installed prefix, matching"
            " `conda export` semantics (enables --no-builds,"
            " --ignore-channels, --from-history)."
        ),
    )

    export_parser.add_argument(
        "--no-builds",
        action="store_true",
        default=False,
        help="Omit build strings from the exported specs (requires --from-prefix).",
    )
    export_parser.add_argument(
        "--ignore-channels",
        action="store_true",
        default=False,
        help="Do not include channel metadata (requires --from-prefix).",
    )
    export_parser.add_argument(
        "--from-history",
        action="store_true",
        default=False,
        help=(
            "Export only explicit specs from the prefix's history"
            " (requires --from-prefix)."
        ),
    )

    list_parser = sub.add_parser(
        "list",
        help="List packages in a workspace environment.",
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

    envs_parser = sub.add_parser(
        "envs",
        help="List environments defined in the workspace.",
        add_help=False,
    )
    add_parser_help(envs_parser)
    add_output_and_prompt_options(envs_parser)
    envs_parser.add_argument(
        "--installed",
        action="store_true",
        default=False,
        help="Only show installed environments.",
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
        help="Package specs to add (e.g. 'numpy>=1.24').",
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
    add_parser_cmd.add_argument(
        "--no-install",
        action="store_true",
        default=False,
        help="Update manifest and lockfile but skip installing into the environment.",
    )
    add_parser_cmd.add_argument(
        "--no-lockfile-update",
        action="store_true",
        default=False,
        help="Only update the manifest; skip solving, lockfile, and install.",
    )
    add_parser_cmd.add_argument(
        "--force-reinstall",
        action="store_true",
        default=False,
        help="Remove and recreate the affected environment(s) from scratch.",
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
    rm_parser.add_argument(
        "--no-install",
        action="store_true",
        default=False,
        help="Update the manifest and lockfile but skip reinstalling the environment.",
    )
    rm_parser.add_argument(
        "--no-lockfile-update",
        action="store_true",
        default=False,
        help="Only update the manifest; skip solving, lockfile, and reinstall.",
    )
    rm_parser.add_argument(
        "--force-reinstall",
        action="store_true",
        default=False,
        help="Remove and recreate the affected environment(s) from scratch.",
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

    activate_parser = sub.add_parser(
        "activate",
        help="Print activation instructions for an environment.",
        add_help=False,
    )
    add_parser_help(activate_parser)
    _accept_json_silently(activate_parser)
    activate_parser.add_argument(
        "-e",
        "--environment",
        default="default",
        help="Environment name (default: default).",
    )

    run_parser = sub.add_parser(
        "run",
        help="Run a command in a workspace environment.",
        add_help=False,
    )
    add_parser_help(run_parser)
    _accept_json_silently(run_parser)
    run_parser.add_argument(
        "-e",
        "--environment",
        default="default",
        help="Environment name (default: default).",
    )
    run_parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command to run (use -- to separate from options).",
    )

    shell_parser = sub.add_parser(
        "shell",
        help="Spawn a new shell with an environment activated.",
        add_help=False,
    )
    add_parser_help(shell_parser)
    _accept_json_silently(shell_parser)
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

    quickstart_parser = sub.add_parser(
        "quickstart",
        help=(
            "Bootstrap a workspace in one command: init (or copy an existing"
            " manifest), add the given specs, install the environment,"
            " and drop into a shell."
        ),
        add_help=False,
    )
    add_parser_help(quickstart_parser)
    add_output_and_prompt_options(quickstart_parser)
    quickstart_parser.add_argument(
        "specs",
        nargs="*",
        default=[],
        help=(
            "Optional package specs to add immediately (same grammar as"
            " `conda workspace add`, e.g. 'python=3.14' 'numpy>=2.4')."
        ),
    )
    quickstart_parser.add_argument(
        "--format",
        choices=["pixi", "conda", "pyproject"],
        default="conda",
        dest="manifest_format",
        help=(
            "Manifest format for the generated workspace (default: conda)."
            " Ignored with a warning when --copy/--clone is used."
        ),
    )
    quickstart_parser.add_argument(
        "--name",
        default=None,
        help="Workspace name (defaults to directory name).",
    )
    quickstart_parser.add_argument(
        "--channel",
        "-c",
        action="append",
        default=None,
        dest="channels",
        help="Channels to include (repeatable, default: conda-forge).",
    )
    quickstart_parser.add_argument(
        "--platform",
        action="append",
        default=None,
        dest="platforms",
        help="Platforms to support (repeatable, auto-detected if omitted).",
    )
    quickstart_parser.add_argument(
        "-e",
        "--environment",
        default="default",
        help="Environment to install and spawn a shell in (default: default).",
    )
    quickstart_parser.add_argument(
        "--force-reinstall",
        action="store_true",
        default=False,
        help="Re-run install from scratch even if the environment exists.",
    )
    quickstart_parser.add_argument(
        "--locked",
        action="store_true",
        default=False,
        help="Install from existing lockfiles, verifying they are up-to-date.",
    )
    quickstart_parser.add_argument(
        "--frozen",
        action="store_true",
        default=False,
        help="Install from existing lockfiles without checking freshness.",
    )
    quickstart_parser.add_argument(
        "--copy",
        "--clone",
        dest="copy_from",
        type=Path,
        default=None,
        help=(
            "Copy a source workspace's manifest (conda.toml / pixi.toml /"
            " pyproject.toml) into the current directory instead of"
            " running `init`. Accepts a directory or a manifest file."
        ),
    )
    quickstart_parser.add_argument(
        "--no-shell",
        action="store_true",
        default=False,
        help="Skip the final `conda workspace shell` step (useful for CI).",
    )

    import_parser = sub.add_parser(
        "import",
        help="Import a manifest from another format into conda.toml.",
        add_help=False,
    )
    add_parser_help(import_parser)
    add_output_and_prompt_options(import_parser)
    import_parser.add_argument(
        "file",
        type=Path,
        help=(
            "Manifest file to import (environment.yml, anaconda-project.yml, "
            "conda-project.yml, pixi.toml, pyproject.toml)."
        ),
    )
    import_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default: conda.toml in the current directory).",
    )


def execute_workspace(args: argparse.Namespace) -> int:
    """Main entry point for ``conda workspace``."""
    subcmd = args.subcmd

    if subcmd is None:
        generate_workspace_parser().print_help()
        return 0

    try:
        return _dispatch_workspace(args, subcmd)
    except (CondaSystemExit, DryRunExit):
        raise
    except CondaError as exc:
        return _handle_error(exc)


def _dispatch_workspace(args: argparse.Namespace, subcmd: str) -> int:
    if subcmd == "init":
        from .workspace.init import execute_init

        return execute_init(args)
    elif subcmd == "install":
        from .workspace.install import execute_install

        return execute_install(args)
    elif subcmd == "lock":
        from .workspace.lock import execute_lock

        return execute_lock(args)
    elif subcmd == "export":
        from .workspace.export import execute_export

        return execute_export(args)
    elif subcmd == "list":
        from .workspace.list import execute_list

        return execute_list(args)
    elif subcmd == "envs":
        from .workspace.list import execute_list

        args.envs = True
        return execute_list(args)
    elif subcmd == "info":
        from .workspace.info import execute_info

        return execute_info(args)
    elif subcmd == "add":
        from .workspace.add import execute_add

        return execute_add(args)
    elif subcmd == "remove":
        from .workspace.remove import execute_remove

        return execute_remove(args)
    elif subcmd == "clean":
        from .workspace.clean import execute_clean

        return execute_clean(args)
    elif subcmd == "activate":
        from .workspace.activate import execute_activate

        return execute_activate(args)
    elif subcmd == "run":
        from .workspace.run import execute_run

        return execute_run(args)
    elif subcmd == "shell":
        from .workspace.shell import execute_shell

        return execute_shell(args)
    elif subcmd == "import":
        from .workspace.import_manifest import execute_import

        return execute_import(args)
    elif subcmd == "quickstart":
        from .workspace.quickstart import execute_quickstart

        return execute_quickstart(args)
    else:
        generate_workspace_parser().print_help()
        return 0


def generate_task_parser() -> argparse.ArgumentParser:
    """Build and return the task parser — used by sphinxarg.ext for docs."""
    parser = argparse.ArgumentParser(
        prog="conda task",
        description="Run, list, and manage project tasks.",
        add_help=False,
    )
    configure_task_parser(parser)
    return parser


def configure_task_parser(parser: argparse.ArgumentParser) -> None:
    """Set up ``conda task`` CLI with subcommands."""
    add_parser_help(parser)

    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=None,
        help="Path to a specific task file instead of auto-detection.",
    )

    sub = parser.add_subparsers(dest="subcmd")

    run_parser = sub.add_parser("run", help="Run a task.", add_help=False)
    add_parser_help(run_parser)
    add_output_and_prompt_options(run_parser)
    run_parser.add_argument(
        "-e",
        "--environment",
        default=None,
        help="Workspace environment to run in.",
    )
    run_parser.add_argument("task_name", help="Name of the task to run.")
    run_parser.add_argument(
        "task_args",
        nargs="*",
        default=[],
        help="Arguments to pass to the task.",
    )
    run_parser.add_argument(
        "--clean-env",
        action="store_true",
        default=False,
        help="Run in a clean environment (minimal env vars).",
    )
    run_parser.add_argument(
        "--skip-deps",
        action="store_true",
        default=False,
        help="Skip dependency tasks, run only the named task.",
    )
    run_parser.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help="Override the working directory for the task.",
    )
    run_parser.add_argument(
        "--templated",
        action="store_true",
        default=False,
        help="Treat the command as a Jinja2 template (for ad-hoc commands).",
    )

    list_parser = sub.add_parser("list", help="List available tasks.", add_help=False)
    add_parser_help(list_parser)
    add_output_and_prompt_options(list_parser)

    add_task_parser = sub.add_parser(
        "add", help="Add a task to the manifest.", add_help=False
    )
    add_parser_help(add_task_parser)
    add_output_and_prompt_options(add_task_parser)
    add_task_parser.add_argument("task_name", help="Name for the new task.")
    add_task_parser.add_argument("cmd", help="Command string for the task.")
    add_task_parser.add_argument(
        "--depends-on",
        nargs="*",
        default=[],
        help="Tasks this task depends on.",
    )
    add_task_parser.add_argument(
        "--description",
        default=None,
        help="Human-readable description.",
    )

    rm_parser = sub.add_parser(
        "remove", help="Remove a task from the manifest.", add_help=False
    )
    add_parser_help(rm_parser)
    add_output_and_prompt_options(rm_parser)
    rm_parser.add_argument("task_name", help="Name of the task to remove.")

    export_parser = sub.add_parser(
        "export",
        help="Export tasks to conda.toml format.",
        add_help=False,
    )
    add_parser_help(export_parser)
    add_output_and_prompt_options(export_parser)
    export_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write to a file instead of stdout.",
    )


def execute_task(args: argparse.Namespace) -> int:
    """Main entry point for ``conda task``."""
    subcmd = args.subcmd

    if subcmd is None:
        if hasattr(args, "task_name") and args.task_name:
            subcmd = "run"
        else:
            generate_task_parser().print_help()
            return 0

    try:
        return _dispatch_task(args, subcmd)
    except (CondaSystemExit, DryRunExit):
        raise
    except CondaError as exc:
        return _handle_error(exc)


def _dispatch_task(args: argparse.Namespace, subcmd: str) -> int:
    if subcmd == "run":
        from .task.run import execute_run

        return execute_run(args)
    elif subcmd == "list":
        from .task.list import execute_list

        return execute_list(args)
    elif subcmd == "add":
        from .task.add import execute_add

        return execute_add(args)
    elif subcmd == "remove":
        from .task.remove import execute_remove

        return execute_remove(args)
    elif subcmd == "export":
        from .task.export import execute_export

        return execute_export(args)
    else:
        generate_task_parser().print_help()
        return 0
