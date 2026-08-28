"""Shared solve/install/lock pipeline for dependency-changing commands.

Given a workspace config and selected environment names, this module
installs packages into those prefixes and resolves every declared
environment for the canonical ``conda.lock``. The same logic backs
``conda workspace install`` as well as the auto-install behaviour of
``conda workspace add``, ``update``, and ``remove``.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from conda.common.io import captured

from ...context import isolated_package_cache
from ...envs import activate_d_scripts, install_environment
from ...exceptions import CondaWorkspacesError
from ...lockfile import (
    LockfileInstallPlan,
    load_lockfile_data,
    lockfile_path,
    render_lockfile,
    validate_lockfile_output,
    write_lockfile,
)
from ...paths import regular_file_generation
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
    require_absent_prefixes: Iterable[str] = (),
    validate_workspace: Callable[[], None] | None = None,
    console: Console,
) -> None:
    """Resolve and lock the desired state before installing selected environments.

    When *no_install* is true the prefixes are not touched but the
    complete canonical lockfile is still regenerated. When *dry_run*
    is true neither the prefixes nor the lockfile are written.
    The rendered lock solution is also used for dry-run validation. When
    *prune* is true, prefix packages and requested specs absent from the
    resolved manifest are removed during execution.
    *update_targets* selects constrained roots for a frozen-installed
    prefix solve and overlays the same environment/platform slices onto
    *baseline_lockfile*. After every solve succeeds, *publish_lockfile*
    can publish that rendered lock with another desired-state file before
    prefix transactions begin. *require_absent_prefixes* names environment
    prefixes that must remain absent through install preflight.

    If new files appear under ``$PREFIX/etc/conda/activate.d/`` and the
    caller is inside a ``conda workspace shell`` session
    (``CONDA_SPAWN=1``), a hint is printed asking the user to re-spawn.
    """
    names = list(env_names)
    required_absent_names = tuple(dict.fromkeys(require_absent_prefixes))
    required_absent = set(required_absent_names)
    if not names and not update_targets:
        return

    output_generation = None
    if not dry_run and publish_lockfile is None:
        output_path = lockfile_path(ctx)
        validate_lockfile_output(ctx, output_path)
        output_generation = regular_file_generation(output_path)

    resolved_all = resolve_all_environments(config)
    for name in names:
        resolved_all[name] = resolve_environment(
            config, name, None if no_install else ctx.platform
        )

    with isolated_package_cache(dry_run):
        rendered_lockfile = None
        if update_targets is not None:
            if not dry_run and publish_lockfile is None:
                raise ValueError("Selective updates require a lockfile publisher")
            validate_lockfile_output(ctx, lockfile_path(ctx))
            with isolated_package_cache(True):
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
        else:
            validate_lockfile_output(ctx, lockfile_path(ctx))
            solve_prefix_context = (
                tempfile.TemporaryDirectory(prefix="conda-workspaces-force-")
                if force_reinstall
                else nullcontext(None)
            )
            with solve_prefix_context as solve_root:
                solve_prefixes = (
                    {name: Path(solve_root) / name for name in names}
                    if solve_root is not None
                    else None
                )
                rendered_lockfile = render_lockfile(
                    ctx,
                    resolved_all,
                    config=config,
                    solve_prefixes=solve_prefixes,
                    dry_run=dry_run,
                )

        assert rendered_lockfile is not None
        rendered_lockfile_data = load_lockfile_data(rendered_lockfile.encode("utf-8"))
        install_plans = {}
        if not no_install:
            for name in names:
                update_names = None
                if update_targets is not None:
                    resolved = resolved_all[name]
                    declared = resolved.resolve_platform_name(ctx.platform)
                    update_names = update_targets.get((name, declared))
                    if not update_names:
                        continue
                install_plans[name] = LockfileInstallPlan.prepare(
                    ctx,
                    name,
                    lockfile_data=rendered_lockfile_data,
                    update_names=update_names,
                    prune=prune,
                    replace_existing=force_reinstall,
                    require_absent=name in required_absent,
                    validate_workspace=validate_workspace,
                )
        for name in required_absent_names:
            if name in install_plans:
                continue
            prefix = ctx.env_prefix(name)
            if LockfileInstallPlan.prefix_identity(prefix) is not None:
                raise CondaWorkspacesError(
                    f"Workspace environment prefix already exists: {prefix}"
                )
        if not dry_run:
            if publish_lockfile is not None:
                publish_lockfile(rendered_lockfile)
            else:
                write_lockfile(
                    ctx,
                    rendered_lockfile,
                    expected_generation=output_generation,
                )

        if not no_install:
            progress_action = "Updating" if update_targets is not None else "Installing"
            if dry_run:
                completed_action = (
                    "Would update" if update_targets is not None else "Would install"
                )
            else:
                completed_action = (
                    "Updated" if update_targets is not None else "Installed"
                )
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
                if force_reinstall and not dry_run:
                    install_plans[name].remove_preflight_prefix(ctx)
                if not dry_run:
                    install_plans[name].execute()
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
        action = "Would update" if dry_run else "Updated"
        console.print(f"[bold cyan]{action}[/bold cyan] [bold]conda.lock[/bold]")
