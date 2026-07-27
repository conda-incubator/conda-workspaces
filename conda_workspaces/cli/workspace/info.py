"""``conda workspace info`` — show workspace or environment details."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from conda.exceptions import ArgumentError
from rich.console import Console
from rich.table import Table
from tomlkit.items import InlineTable
from tomlkit.items import Table as TomlTable

from ...envs import get_environment_info, list_installed_packages
from ...exceptions import WorkspaceParseError
from ...lockfile import lockfile_status
from ...manifests import find_parser
from ...models import LockfileStatus, redact_channel_name, redact_url_text
from ...resolver import known_platforms, resolve_all_environments, resolve_environment
from .. import status
from . import workspace_context_from_args
from .dependencies import (
    DependencyLocation,
    workspace_toml_source,
)
from .list import package_table

if TYPE_CHECKING:
    import argparse

    import tomlkit

    from ...context import WorkspaceContext
    from ...models import WorkspaceConfig


def execute_info(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Show workspace overview or per-environment details."""
    config, ctx = workspace_context_from_args(args)

    if console is None:
        console = Console(highlight=False)

    env_name = getattr(args, "environment", None)
    json_output = getattr(args, "json", False)
    include_packages = getattr(args, "packages", False)
    if include_packages and env_name is not None:
        raise ArgumentError("--packages is only available for the workspace overview.")

    if env_name is None:
        return _show_workspace_info(
            config,
            ctx,
            console,
            json_output,
            include_packages,
        )
    return _show_env_info(
        config,
        ctx,
        env_name,
        console,
        json_output,
    )


def _show_workspace_info(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    console: Console,
    json_output: bool,
    include_packages: bool,
) -> int:
    """Show workspace-level overview."""
    # Resolving is cheap (no solver, just feature merging) and lets us
    # surface the reachable platform set when features broaden it
    # beyond ``config.platforms``.
    resolved_envs = resolve_all_environments(config)
    known = sorted(known_platforms(config, resolved_envs.values()))

    info: dict[str, object] = {
        "manifest": config.manifest_path,
        "name": config.name or "(unnamed)",
        "version": config.version or "",
        "description": config.description or "",
        "channels": [redact_channel_name(ch) for ch in config.channels],
        "platforms": config.platforms,
        "known_platforms": known,
        "environments": list(config.environments.keys()),
        "features": list(config.features.keys()),
    }

    lock = lockfile_status(ctx, config)
    info["lockfile_status"] = lock.status
    if lock.reason:
        info["lockfile_reason"] = lock.reason

    if json_output:
        info["environment_details"] = _environment_details(
            config,
            ctx,
            include_packages=include_packages,
        )
        print(json.dumps(info), file=console.file)
    else:
        table = Table(show_header=False, show_edge=False, pad_edge=False)
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("Manifest", status.escape_for_console(info["manifest"]))
        table.add_row("Name", status.escape_for_console(info["name"]))
        if info["version"]:
            table.add_row("Version", status.escape_for_console(info["version"]))
        if info["description"]:
            table.add_row(
                "Description",
                status.escape_for_console(info["description"]),
            )
        table.add_row(
            "Channels",
            status.escape_for_console(", ".join(info["channels"]) or "(none)"),
        )
        table.add_row(
            "Platforms",
            status.escape_for_console(", ".join(info["platforms"]) or "(all)"),
        )
        # Only surface the reachable set when a feature has broadened
        # it; otherwise the row is redundant with "Platforms".
        if set(known) != set(info["platforms"]):
            table.add_row(
                "Known Platforms",
                status.escape_for_console(", ".join(known) or "(none)"),
            )
        table.add_row(
            "Environments",
            status.escape_for_console(", ".join(info["environments"])),
        )
        table.add_row(
            "Features",
            status.escape_for_console(", ".join(info["features"]) or "(none)"),
        )
        status_style = {
            LockfileStatus.UP_TO_DATE: "green",
            LockfileStatus.OUT_OF_DATE: "yellow",
            LockfileStatus.MISSING: "red",
        }[lock.status]
        lockfile_label = (
            f"[{status_style}]{status.escape_for_console(lock.status)}[/{status_style}]"
        )
        if lock.reason:
            lockfile_label += f" ({status.escape_for_console(lock.reason)})"
        table.add_row("Lockfile", lockfile_label)
        console.print(table)
        if include_packages:
            for env_name in config.environments:
                console.print(
                    f"\n[bold]Packages in {status.escape_for_console(env_name)}:[/bold]"
                )
                if not ctx.env_exists(env_name):
                    console.print("  (not installed)")
                    continue
                packages = list_installed_packages(ctx, env_name)
                if packages:
                    console.print(package_table(packages))
                else:
                    console.print("  (none)")

    return 0


def _show_env_info(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    env_name: str,
    console: Console,
    json_output: bool,
) -> int:
    """Show details for a single environment."""
    resolved = resolve_environment(config, env_name, ctx.platform)
    install_info = get_environment_info(ctx, env_name)

    info: dict[str, object] = {
        "name": env_name,
        "prefix": str(ctx.env_prefix(env_name)),
        "installed": install_info["exists"],
        "features": config.environments[env_name].features,
        "no_default_feature": config.environments[env_name].no_default_feature,
        "channels": [redact_channel_name(ch) for ch in resolved.channels],
        "platforms": resolved.platforms,
        "channel_priority": resolved.channel_priority,
        "conda_dependencies": {
            name: redact_url_text(dep.conda_build_form())
            for name, dep in resolved.conda_dependencies.items()
        },
        "pypi_dependencies": {
            name: str(dep.redacted())
            for name, dep in resolved.pypi_dependencies.items()
        },
    }

    if install_info["exists"]:
        info["packages_installed"] = install_info.get("packages", 0)

    if json_output:
        print(json.dumps(info), file=console.file)
    else:
        table = Table(show_header=False, show_edge=False, pad_edge=False)
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("Environment", status.escape_for_console(info["name"]))
        table.add_row("Prefix", status.escape_for_console(info["prefix"]))
        table.add_row("Installed", "yes" if info["installed"] else "no")
        if info["installed"]:
            table.add_row("Packages", str(info.get("packages_installed", "?")))
        table.add_row(
            "Channels",
            status.escape_for_console(", ".join(info["channels"]) or "(none)"),
        )
        table.add_row(
            "Platforms",
            status.escape_for_console(", ".join(info["platforms"]) or "(all)"),
        )
        if info["channel_priority"]:
            table.add_row(
                "Channel priority",
                status.escape_for_console(info["channel_priority"]),
            )
        console.print(table)

        if info["conda_dependencies"]:
            console.print("\n[bold]Conda dependencies:[/bold]")
            for _name, spec in sorted(info["conda_dependencies"].items()):
                console.print(f"  {status.escape_for_console(spec)}")

        if info["pypi_dependencies"]:
            console.print("\n[bold]PyPI dependencies:[/bold]")
            for _name, spec in sorted(info["pypi_dependencies"].items()):
                console.print(f"  {status.escape_for_console(spec)}")

    return 0


def _environment_details(
    config: WorkspaceConfig,
    ctx: WorkspaceContext,
    *,
    include_packages: bool,
) -> list[dict[str, object]]:
    """Return complete environment composition for structured workspace info."""
    manifest_path = Path(config.manifest_path)
    document = find_parser(manifest_path).load_toml(manifest_path)
    source, namespace = workspace_toml_source(
        document,
        manifest_path,
        create=False,
    )
    if source is None:
        raise WorkspaceParseError(
            manifest_path,
            "Could not locate the selected workspace tables",
        )

    details: list[dict[str, object]] = []
    for env_name, environment in config.environments.items():
        base = resolve_environment(config, env_name)
        resolutions = []
        for platform in base.target_platforms(fallback=ctx.platform):
            resolved = resolve_environment(config, env_name, platform)
            locations: dict[str, dict[str, DependencyLocation]] = {
                "dependencies": {},
                "pypi-dependencies": {},
            }
            for location in DependencyLocation.precedence(
                config,
                environment,
                platform,
            ):
                table = location.find_table(source)
                if table is None:
                    continue
                for dependency_key, winners in locations.items():
                    winners.update(
                        dict.fromkeys(table.get(dependency_key, {}), location)
                    )
            resolutions.append(
                {
                    "platform": platform,
                    "subdir": resolved.platform_subdir(platform),
                    "conda_dependencies": {
                        name: _dependency_detail(
                            source,
                            namespace,
                            config,
                            "dependencies",
                            name,
                            redact_url_text(dep.conda_build_form()),
                            locations["dependencies"].get(name),
                        )
                        for name, dep in resolved.conda_dependencies.items()
                    },
                    "pypi_dependencies": {
                        name: _dependency_detail(
                            source,
                            namespace,
                            config,
                            "pypi-dependencies",
                            name,
                            dep.redacted().to_toml(),
                            locations["pypi-dependencies"].get(name),
                        )
                        for name, dep in resolved.pypi_dependencies.items()
                    },
                }
            )

        installed = ctx.env_exists(env_name)
        detail: dict[str, object] = {
            "name": env_name,
            "features": environment.features,
            "no_default_feature": environment.no_default_feature,
            "prefix": str(ctx.env_prefix(env_name)),
            "installed": installed,
            "channels": [redact_channel_name(channel) for channel in base.channels],
            "platforms": base.platforms,
            "channel_priority": base.channel_priority,
            "resolutions": resolutions,
        }
        if include_packages:
            detail["packages"] = (
                list_installed_packages(ctx, env_name) if installed else []
            )
        details.append(detail)
    return details


def _dependency_detail(
    source: tomlkit.TOMLDocument | TomlTable | InlineTable,
    namespace: tuple[str, ...],
    config: WorkspaceConfig,
    dependency_key: str,
    name: str,
    spec: object,
    location: DependencyLocation | None,
) -> dict[str, object]:
    """Return one resolved dependency and its winning manifest declaration."""
    if location is None:
        raise WorkspaceParseError(
            config.manifest_path,
            f"Could not locate the declaration for dependency '{name}'",
        )

    provenance = {
        "table": location.table_name(dependency_key, namespace),
    }
    table = location.find_table(source)
    dependency_table = table.get(dependency_key, {}) if table is not None else {}
    declaration = dependency_table.get(name)
    if (
        dependency_key == "dependencies"
        and isinstance(declaration, (TomlTable, InlineTable))
        and declaration.get("workspace") is True
    ):
        provenance["inherited_from"] = DependencyLocation().table_name(
            "dependencies",
            (*namespace, "workspace"),
        )
    return {
        "spec": spec,
        "provenance": provenance,
    }
