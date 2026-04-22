"""Shared solve/install/lock pipeline used by ``install``, ``add``, and ``remove``.

Given a workspace config and a list of environment names, this module
resolves each environment, installs packages into its prefix, and
updates ``conda.lock``.  The same logic backs ``conda workspace install``
as well as the auto-install behaviour of ``conda workspace add`` and
``conda workspace remove``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ...envs import activate_d_scripts, install_environment
from ...lockfile import generate_lockfile
from ...resolver import resolve_environment
from .. import status

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rich.console import Console

    from ...context import WorkspaceContext
    from ...models import WorkspaceConfig


def affected_environments(
    config: WorkspaceConfig,
    target_feature: str | None,
) -> list[str]:
    """Return environment names whose composition includes *target_feature*.

    When *target_feature* is ``None`` or ``"default"`` the default feature
    was touched, so every environment that does not set
    ``no-default-feature = true`` is affected.  Otherwise, every
    environment whose ``features`` list contains *target_feature* is
    affected.
    """
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
    console: Console,
) -> None:
    """Resolve, install, and lock the given workspace environments.

    When *no_install* is true the prefixes are not touched but the
    lockfile is still regenerated.  When *dry_run* is true neither the
    prefixes nor the lockfile are written.  ``install_environment``
    receives *force_reinstall* / *dry_run* verbatim.

    If new files appear under ``$PREFIX/etc/conda/activate.d/`` and the
    caller is inside a ``conda workspace shell`` session
    (``CONDA_SPAWN=1``), a hint is printed asking the user to re-spawn.
    """
    names = list(env_names)
    if not names:
        return

    resolved_all = {
        name: resolve_environment(config, name, ctx.platform) for name in names
    }

    if not no_install:
        for i, (name, resolved) in enumerate(resolved_all.items()):
            if i > 0:
                console.print()
            status.message(
                console,
                "Installing",
                "environment",
                name,
                style="bold blue",
                ellipsis=True,
            )
            prefix = ctx.env_prefix(name)
            before = activate_d_scripts(prefix)

            install_environment(
                ctx,
                resolved,
                force_reinstall=force_reinstall,
                dry_run=dry_run,
            )
            status.message(console, "Installed", "environment", name)

            if not dry_run:
                new_scripts = activate_d_scripts(prefix) - before
                if new_scripts and os.environ.get("CONDA_SPAWN") == "1":
                    console.print(
                        "[bold yellow]Note:[/bold yellow] new activation scripts"
                        " were installed. Exit and re-run"
                        " [bold]conda workspace shell[/bold] to pick them up."
                    )

    if not dry_run:
        console.print()
        console.print(
            "[bold blue]Updating[/bold blue] [bold]conda.lock[/bold][dim]...[/dim]"
        )
        generate_lockfile(ctx, resolved_all)
        console.print("[bold cyan]Updated[/bold cyan] [bold]conda.lock[/bold]")
