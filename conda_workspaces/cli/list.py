"""``conda workspace list`` — list packages or environments."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..context import WorkspaceContext
from ..envs import list_installed_environments
from ..exceptions import EnvironmentNotFoundError, EnvironmentNotInstalledError
from ..parsers import detect_and_parse

if TYPE_CHECKING:
    import argparse

    from ..models import WorkspaceConfig


def execute_list(args: argparse.Namespace) -> int:
    """List packages in an environment, or environments in the workspace."""
    manifest_path = getattr(args, "file", None)
    _, config = detect_and_parse(manifest_path)
    ctx = WorkspaceContext(config)

    json_output = getattr(args, "json", False)

    if getattr(args, "envs", False):
        installed_only = getattr(args, "installed", False)
        return _list_environments(config, ctx, json_output, installed_only)

    env_name = getattr(args, "environment", "default")
    return _list_packages(config, ctx, env_name, json_output)


def _list_packages(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    env_name: str,
    json_output: bool,
) -> int:
    """List installed packages in an environment."""
    from conda.core.envs_manager import PrefixData

    if env_name not in config.environments:
        raise EnvironmentNotFoundError(env_name, list(config.environments.keys()))

    if not ctx.env_exists(env_name):
        raise EnvironmentNotInstalledError(env_name)

    prefix = ctx.env_prefix(env_name)
    pd = PrefixData(str(prefix))
    records = sorted(pd.iter_records(), key=lambda r: r.name)

    if json_output:
        rows = [
            {"name": r.name, "version": r.version, "build": r.build}
            for r in records
        ]
        print(json.dumps(rows, indent=2))
    else:
        if not records:
            print(f"No packages installed in '{env_name}'.")
            return 0

        print(f"{'Name':<30} {'Version':<20} {'Build'}")
        print("-" * 70)
        for r in records:
            print(f"{r.name:<30} {r.version:<20} {r.build}")

    return 0


def _list_environments(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    json_output: bool,
    installed_only: bool,
) -> int:
    """List environments defined in the workspace."""
    installed = set(list_installed_environments(ctx))

    rows: list[dict[str, str | bool | list[str]]] = []
    for name, env in sorted(config.environments.items()):
        if installed_only and name not in installed:
            continue
        rows.append(
            {
                "name": name,
                "features": env.features,
                "solve_group": env.solve_group or "",
                "installed": name in installed,
            }
        )

    if json_output:
        print(json.dumps(rows, indent=2))
    else:
        if not rows:
            print("No environments found.")
            return 0

        print(f"{'Name':<20} {'Features':<30} {'Solve Group':<15} {'Installed'}")
        print("-" * 75)
        for row in rows:
            feats = ", ".join(row["features"]) if row["features"] else "(default)"  # type: ignore[arg-type]
            status = "yes" if row["installed"] else "no"
            print(f"{row['name']:<20} {feats:<30} {row['solve_group']:<15} {status}")

    return 0
