"""``conda workspace remove`` — remove dependencies from the manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit
from rich.console import Console

from ...context import WorkspaceContext
from ...exceptions import CondaWorkspacesError
from ...manifests import detect_workspace_file, find_parser
from . import workspace_manifest_path_from_args
from .dependencies import (
    DependencyLocation,
    dependency_declarations,
    workspace_toml_source,
)
from .sync import affected_environments, sync_environments

if TYPE_CHECKING:
    import argparse

    from tomlkit.items import InlineTable, Table

    from .dependencies import DependencyDeclaration


def execute_remove(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Remove dependencies from the workspace manifest."""
    if console is None:
        console = Console(highlight=False)
    selected_manifest_path = workspace_manifest_path_from_args(args)
    manifest_path = selected_manifest_path or detect_workspace_file()
    specs = args.specs
    is_pypi = getattr(args, "pypi", False)
    feature = getattr(args, "feature", None)
    environment = getattr(args, "environment", None)
    platform = getattr(args, "platform", None)
    dry_run = getattr(args, "dry_run", False)
    location = DependencyLocation.from_selectors(
        feature=feature,
        environment=environment,
        platform=platform,
    )

    text = manifest_path.read_text(encoding="utf-8")
    doc = tomlkit.loads(text)
    dep_key = "pypi-dependencies" if is_pypi else "dependencies"

    source, namespace = workspace_toml_source(doc, manifest_path, create=False)
    parser = find_parser(manifest_path)
    if source is not None:
        current = parser.parse_data(doc.unwrap(), manifest_path)
        if location.environment is not None:
            current.get_environment(location.environment)
        location.validate_platform(current, source, allow_existing=True)
        selected = location.find_table(source)
        selected_dependencies = (
            selected.get(dep_key, {}) if selected is not None else {}
        )
        wrong_locations: dict[str, list[DependencyDeclaration]] = {}
        for name in specs:
            if name in selected_dependencies:
                continue
            declarations = dependency_declarations(source, name)
            if declarations:
                wrong_locations[name] = declarations
        if wrong_locations:
            names = ", ".join(f"'{name}'" for name in wrong_locations)
            noun = "Dependency" if len(wrong_locations) == 1 else "Dependencies"
            verb = "is" if len(wrong_locations) == 1 else "are"
            hints: list[str] = []
            for name, declarations in wrong_locations.items():
                for declaration in declarations:
                    declaration_key = (
                        "pypi-dependencies" if declaration.pypi else "dependencies"
                    )
                    command = declaration.location.command(
                        "remove",
                        name,
                        pypi=declaration.pypi,
                        manifest_path=selected_manifest_path,
                    )
                    table_name = declaration.location.table_name(
                        declaration_key,
                        namespace,
                    )
                    hints.append(f"Run '{command}' for {table_name}.")
            raise CondaWorkspacesError(
                f"{noun} {names} {verb} not declared directly in"
                f" {location.table_name(dep_key, namespace)}.",
                hints=hints,
            )
    removed = (
        _remove_from_toml(source, specs, dep_key, location)
        if source is not None
        else []
    )

    if removed:
        config = parser.parse_data(doc.unwrap(), manifest_path)
        if not dry_run:
            manifest_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        label = "PyPI" if is_pypi else "conda"
        n = len(removed)
        noun = "dependency" if n == 1 else "dependencies"
        action = "Would remove" if dry_run else "Removed"
        console.print(
            f"[bold cyan]{action}[/bold cyan] {n} {label} {noun}"
            f" from {location.display_name} in [bold]{manifest_path.name}[/bold]"
        )
    else:
        console.print(
            "[bold yellow]No matching dependencies found.[/bold yellow]"
            " Check the package name, or use [bold]--pypi[/bold]"
            " for PyPI dependencies."
        )
        return 0

    if getattr(args, "no_lockfile_update", False):
        return 0

    ctx = WorkspaceContext(config)
    env_names = affected_environments(
        config,
        location.feature,
        target_environment=location.environment,
    )
    if env_names:
        console.print()
        sync_environments(
            config,
            ctx,
            env_names,
            no_install=getattr(args, "no_install", False),
            force_reinstall=getattr(args, "force_reinstall", False),
            dry_run=dry_run,
            prune=True,
            console=console,
        )
    return 0


def _remove_from_toml(
    doc: tomlkit.TOMLDocument | Table | InlineTable,
    specs: list[str],
    dep_key: str,
    location: DependencyLocation,
) -> list[str]:
    """Remove deps from a pixi.toml or conda.toml document."""
    target = location.find_table(doc)
    deps = target.get(dep_key, {}) if target is not None else {}
    removed: list[str] = []
    for name in specs:
        if name in deps:
            del deps[name]
            removed.append(name)
    return removed
