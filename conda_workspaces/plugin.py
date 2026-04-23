"""Conda plugin registration for conda-workspaces.

This module is imported on *every* conda invocation via the entry point
system.  Only ``hookimpl`` and type imports are used at module level —
everything else is lazily imported inside the hooks to keep the
overhead under 1 ms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.plugins import hookimpl
from conda.plugins.types import (
    CondaEnvironmentExporter,
    CondaEnvironmentSpecifier,
    CondaSubcommand,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


@hookimpl
def conda_subcommands() -> Iterable[CondaSubcommand]:
    from .cli import (
        configure_task_parser,
        configure_workspace_parser,
        execute_task,
        execute_workspace,
    )

    yield CondaSubcommand(
        name="workspace",
        summary="Manage project-scoped multi-environment workspaces.",
        action=execute_workspace,  # ty: ignore[invalid-argument-type]
        configure_parser=configure_workspace_parser,
    )
    yield CondaSubcommand(
        name="task",
        summary="Run, list, and manage project tasks.",
        action=execute_task,  # ty: ignore[invalid-argument-type]
        configure_parser=configure_task_parser,
    )


@hookimpl
def conda_environment_specifiers() -> Iterable[CondaEnvironmentSpecifier]:
    from . import env_spec, lockfile

    # TODO: once CondaEnvironmentSpecifier grows aliases/default_filenames
    # fields (conda/conda#15928), pass env_spec.ALIASES /
    # lockfile.ALIASES / *.DEFAULT_FILENAMES through as well.  Until
    # then, only the canonical FORMAT is registered as a spec name.
    yield CondaEnvironmentSpecifier(
        name=env_spec.FORMAT,
        environment_spec=env_spec.CondaWorkspaceSpec,
    )
    yield CondaEnvironmentSpecifier(
        name=lockfile.FORMAT,
        environment_spec=lockfile.CondaLockLoader,
    )


@hookimpl
def conda_environment_exporters() -> Iterable[CondaEnvironmentExporter]:
    from . import export, lockfile

    yield CondaEnvironmentExporter(
        name=lockfile.FORMAT,
        aliases=lockfile.ALIASES,
        default_filenames=lockfile.DEFAULT_FILENAMES,
        multiplatform_export=export.multiplatform_export,
    )


@hookimpl
def conda_pre_commands():
    from conda.plugins.types import CondaPreCommand

    yield CondaPreCommand(
        name="conda-workspaces-install-hint",
        action=_install_hint,
        run_for={"install"},
    )


def _install_hint(command: str) -> None:
    """Warn when ``conda install`` runs in a workspace directory.

    Only triggers when the target environment is *not* a workspace
    environment (e.g. when the user is installing into base).
    """
    import logging
    from pathlib import Path

    log = logging.getLogger(__name__)

    try:
        from .manifests import detect_workspace_file

        detect_workspace_file()
    except Exception:
        return

    from conda.base.context import context

    target = Path(context.target_prefix)

    try:
        from .manifests import detect_and_parse

        _, config = detect_and_parse()
        from .context import WorkspaceContext

        ctx = WorkspaceContext(config)
        for env_name in config.environments:
            if ctx.env_prefix(env_name).resolve() == target.resolve():
                return
    except Exception:
        pass

    log.info(
        "Hint: You are in a workspace directory. "
        "To add dependencies to your workspace, use: "
        "conda workspace add <package>"
    )
