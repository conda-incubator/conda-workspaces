"""Shared solve/install/lock pipeline for dependency-changing commands.

Given a workspace config and selected environment names, this module
installs packages into those prefixes and resolves every declared
environment for the canonical ``conda.lock``. The same logic backs
``conda workspace install`` as well as the auto-install behaviour of
``conda workspace add``, ``update``, and ``remove``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from conda.common.io import captured

from ...envs import activate_d_scripts, install_environment
from ...lockfile import (
    generate_lockfile,
    lockfile_path,
    render_lockfile,
    validate_lockfile_output,
)
from ...resolver import resolve_all_environments, resolve_environment
from .. import status

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import Any

    from rich.console import Console

    from ...context import WorkspaceContext
    from ...models import WorkspaceConfig


def affected_environments(
    config: WorkspaceConfig,
    target_feature: str | None,
    *,
    target_environment: str | None = None,
) -> list[str]:
    """Return environment names affected by one explicit mutation location.

    An environment-local mutation affects only that environment. A default
    or named feature mutation affects every environment that composes it.
    """
    if target_environment is not None:
        return [target_environment] if target_environment in config.environments else []

    names: list[str] = []
    for name, env in config.environments.items():
        if target_feature in (None, "default"):
            if not env.no_default_feature:
                names.append(name)
        elif target_feature in env.features:
            names.append(name)
    return names


def sync_environments(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    env_names: Iterable[str],
    *,
    no_install: bool = False,
    force_reinstall: bool = False,
    dry_run: bool = False,
    prune: bool = False,
    baseline_lockfile: dict[str, Any] | None = None,
    update_targets: dict[tuple[str, str], set[str]] | None = None,
    publish_lockfile: Callable[[str], None] | None = None,
    console: Console,
) -> None:
    """Resolve and install the selected environments, then lock the workspace.

    When *no_install* is true the prefixes are not touched but the
    complete canonical lockfile is still regenerated. When *dry_run*
    is true neither the prefixes nor the lockfile are written.
    ``install_environment`` receives *force_reinstall* / *dry_run*
    verbatim. When *prune* is true, requested prefix specs absent from
    the resolved manifest are removed before installation.
    *update_targets* selects constrained roots for a frozen-installed
    prefix solve and overlays the same environment/platform slices onto
    *baseline_lockfile*. After every solve succeeds, *publish_lockfile*
    can publish that rendered lock with another desired-state file before
    prefix transactions begin.

    If new files appear under ``$PREFIX/etc/conda/activate.d/`` and the
    caller is inside a ``conda workspace shell`` session
    (``CONDA_SPAWN=1``), a hint is printed asking the user to re-spawn.
    """
    names = list(env_names)
    if not names and not update_targets:
        return

    resolved_all = resolve_all_environments(config)
    for name in names:
        resolved_all[name] = resolve_environment(
            config, name, None if no_install else ctx.platform
        )

    rendered_lockfile = None
    if update_targets is not None:
        if not dry_run and publish_lockfile is None:
            raise ValueError("Selective updates require a lockfile publisher")
        validate_lockfile_output(ctx, lockfile_path(ctx))
        if not no_install and not dry_run:
            with captured():
                for name in names:
                    resolved = resolved_all[name]
                    declared = resolved.resolve_platform_name(ctx.platform)
                    update_names = update_targets.get((name, declared))
                    if update_names:
                        install_environment(
                            ctx,
                            resolved,
                            dry_run=True,
                            update_names=update_names,
                        )
        rendered_lockfile = render_lockfile(
            ctx,
            resolved_all,
            config=config,
            baseline_data=baseline_lockfile,
            update_targets=update_targets,
            dry_run=True,
        )
        if not dry_run and publish_lockfile is not None:
            publish_lockfile(rendered_lockfile)

    solve_prefixes = {}

    if not no_install:
        progress_action = "Updating" if update_targets is not None else "Installing"
        if dry_run:
            completed_action = (
                "Would update" if update_targets is not None else "Would install"
            )
        else:
            completed_action = "Updated" if update_targets is not None else "Installed"
        for i, name in enumerate(names):
            resolved = resolved_all[name]
            if i > 0:
                console.print()
            status.message(
                console,
                progress_action,
                "environment",
                name,
                style="bold blue",
                ellipsis=True,
            )
            prefix = ctx.env_prefix(name)
            before = activate_d_scripts(prefix)

            update_names = None
            if update_targets is not None:
                declared = resolved.resolve_platform_name(ctx.platform)
                update_names = update_targets.get((name, declared))
                if not update_names:
                    continue
            solve_prefix = install_environment(
                ctx,
                resolved,
                force_reinstall=force_reinstall,
                dry_run=dry_run,
                prune=prune,
                update_names=update_names,
            )
            if dry_run and force_reinstall:
                solve_prefixes[name] = solve_prefix
            status.message(
                console,
                completed_action,
                "environment",
                name,
            )

            if not dry_run:
                new_scripts = activate_d_scripts(prefix) - before
                if new_scripts and os.environ.get("CONDA_SPAWN") == "1":
                    console.print(
                        "[bold yellow]Note:[/bold yellow] new activation scripts"
                        " were installed. Exit and re-run"
                        " [bold]conda workspace shell[/bold] to pick them up."
                    )

    console.print()
    progress = "Resolving" if dry_run else "Updating"
    console.print(
        f"[bold blue]{progress}[/bold blue] [bold]conda.lock[/bold][dim]...[/dim]"
    )
    if rendered_lockfile is None:
        generate_lockfile(
            ctx,
            resolved_all,
            config=config,
            dry_run=dry_run,
            solve_prefixes=solve_prefixes or None,
        )
    action = "Would update" if dry_run else "Updated"
    console.print(f"[bold cyan]{action}[/bold cyan] [bold]conda.lock[/bold]")
