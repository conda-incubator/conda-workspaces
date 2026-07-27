"""``conda workspace remove`` — remove dependencies from the manifest."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from rich.console import Console

from ...context import WorkspaceContext
from ...exceptions import CondaWorkspacesError
from ...manifests import detect_workspace_file, find_parser
from ...publication import WorkspacePublication
from .. import status
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
    dry_run = getattr(args, "dry_run", False)
    requested_manifest_path = getattr(args, "manifest_file", None)
    if requested_manifest_path is not None:
        WorkspacePublication.validate_manifest_path(Path(requested_manifest_path))
    selected_manifest_path = workspace_manifest_path_from_args(
        args,
        for_mutation=True,
    )
    manifest_path = selected_manifest_path or detect_workspace_file()
    WorkspacePublication.validate_manifest_path(manifest_path)
    specs = args.specs
    is_pypi = getattr(args, "pypi", False)
    feature = getattr(args, "feature", None)
    environment = getattr(args, "environment", None)
    platform = getattr(args, "platform", None)
    location = DependencyLocation.from_selectors(
        feature=feature,
        environment=environment,
        platform=platform,
    )

    parser = find_parser(manifest_path)
    original_text = parser.read_manifest_text(manifest_path)
    doc = parser.parse_toml_text_with_redacted_errors(original_text, manifest_path)
    dep_key = "pypi-dependencies" if is_pypi else "dependencies"

    source, namespace = workspace_toml_source(doc, manifest_path, create=False)
    if source is not None:
        current = parser.parse_data_with_redacted_errors(doc.unwrap(), manifest_path)
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
        parser.validate_no_url_credentials(
            doc.unwrap(),
            manifest_path,
            content=tomlkit.dumps(doc),
        )
        config = parser.parse_data_with_redacted_errors(doc.unwrap(), manifest_path)
        updated_text = tomlkit.dumps(doc)
    else:
        console.print(
            "[bold yellow]No matching dependencies found.[/bold yellow]"
            " Check the package name, or use [bold]--pypi[/bold]"
            " for PyPI dependencies."
        )
        return 0

    ctx = WorkspaceContext(config)
    env_names = affected_environments(
        config,
        location.feature,
        target_environment=location.environment,
    )
    publication = WorkspacePublication(
        ctx,
        manifest_path,
        original_text,
        updated_text,
        "remove",
    )
    publication_context = nullcontext() if dry_run else publication.guard()
    with publication_context:
        label = "PyPI" if is_pypi else "conda"
        n = len(removed)
        noun = "dependency" if n == 1 else "dependencies"
        action = "Would remove" if dry_run else "Removed"
        console.print(
            f"[bold cyan]{action}[/bold cyan] {n} {label} {noun}"
            f" from {status.escape_for_console(location.display_name)} in [bold]"
            f"{status.escape_for_console(manifest_path.name)}[/bold]"
        )

        if getattr(args, "no_lockfile_update", False):
            if not dry_run:
                publication.publish_manifest()
            return 0

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
                publish_lockfile=publication.publish_lockfile,
                console=console,
            )
        elif not dry_run:
            publication.publish_manifest()
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
