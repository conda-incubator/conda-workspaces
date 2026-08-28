"""``conda workspace remove`` — remove dependencies or an environment."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from conda.cli.common import is_active_prefix
from conda.reporters import confirm_yn
from rich.console import Console

from ...context import WorkspaceContext
from ...envs import remove_environment
from ...exceptions import CondaWorkspacesError
from ...lockfile import lockfile_path, render_lockfile, validate_lockfile_output
from ...manifests import detect_task_file, detect_workspace_file, find_parser
from ...paths import output_paths_collide
from ...publication import WorkspacePublication
from ...resolver import resolve_all_environments
from .. import status
from . import workspace_manifest_path_from_args
from .dependencies import (
    DependencyLocation,
    dependency_declarations,
    reject_legacy_default_feature,
    workspace_toml_source,
)
from .sync import affected_environments, sync_environments

if TYPE_CHECKING:
    import argparse

    from tomlkit.items import InlineTable, Table

    from .dependencies import DependencyDeclaration


def execute_remove(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Remove dependencies or a complete environment from the workspace."""
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
    remove_all = getattr(args, "all", False)
    if remove_all:
        incompatible = [
            option
            for enabled, option in (
                (bool(specs), "package specs"),
                (feature is not None, "--feature"),
                (platform is not None, "--platform"),
                (is_pypi, "--pypi"),
                (getattr(args, "no_install", False), "--no-install"),
                (
                    getattr(args, "no_lockfile_update", False),
                    "--no-lockfile-update",
                ),
                (getattr(args, "force_reinstall", False), "--force-reinstall"),
            )
            if enabled
        ]
        if environment is None:
            raise CondaWorkspacesError(
                "--all requires -e/--environment to select an environment."
            )
        if incompatible:
            raise CondaWorkspacesError(
                "--all cannot be combined with " + ", ".join(incompatible) + "."
            )
    elif not specs:
        raise CondaWorkspacesError(
            "Package names are required unless --all removes a complete environment.",
            hints=["Pass both '-e NAME' and '--all' to remove an environment."],
        )
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
    if remove_all:
        assert environment is not None
        if source is None:
            raise CondaWorkspacesError(
                f"Environment '{environment}' is not defined in the workspace."
            )
        reject_legacy_default_feature(source)
        current = parser.parse_data_with_redacted_errors(doc.unwrap(), manifest_path)
        current.get_environment(environment)
        if environment == "default":
            raise CondaWorkspacesError(
                "The implicit 'default' environment cannot be removed.",
                hints=[
                    (
                        "Remove its dependencies or change its explicit declaration"
                        " instead."
                    )
                ],
            )

        task_manifest_path = detect_task_file(
            manifest_path.parent,
            reject_symlinks=True,
        )
        project_task_sets = [parser.parse_tasks_data(doc.unwrap())]
        if task_manifest_path is not None and not output_paths_collide(
            task_manifest_path,
            manifest_path,
        ):
            project_task_sets.append(
                find_parser(task_manifest_path).parse_tasks(task_manifest_path)
            )
        references: list[str] = []
        for project_tasks in project_task_sets:
            for task_name, task in project_tasks.items():
                if task.default_environment == environment:
                    references.append(
                        f"Task '{task_name}' sets default-environment to"
                        f" '{environment}'."
                    )
                for index, dependency in enumerate(task.depends_on):
                    if dependency.environment == environment:
                        references.append(
                            f"Task '{task_name}' depends-on entry {index + 1} selects"
                            f" environment '{environment}'."
                        )
                for target, override in (task.platforms or {}).items():
                    for index, dependency in enumerate(override.depends_on or []):
                        if dependency.environment == environment:
                            references.append(
                                f"Task '{task_name}' target '{target}' depends-on"
                                f" entry {index + 1} selects environment"
                                f" '{environment}'."
                            )
        if references:
            raise CondaWorkspacesError(
                f"Environment '{environment}' is still referenced by workspace tasks.",
                hints=references,
            )

        environments = source.get("environments")
        if environments is None or environment not in environments:
            raise CondaWorkspacesError(
                f"Environment '{environment}' has no removable manifest declaration."
            )
        del environments[environment]
        parser.validate_no_url_credentials(
            doc.unwrap(),
            manifest_path,
            content=tomlkit.dumps(doc),
        )
        config = parser.parse_data_with_redacted_errors(doc.unwrap(), manifest_path)
        updated_text = tomlkit.dumps(doc)
        ctx = WorkspaceContext(config)
        prefix = ctx.env_prefix(environment)
        if is_active_prefix(str(prefix)):
            raise CondaWorkspacesError(
                f"Environment '{environment}' is active and cannot be removed.",
                hints=["Deactivate it and run the command again."],
            )

        envs_identity = ctx.envs_dir_identity()
        prefix_identity = next(
            (
                identity
                for installed_prefix, identity in ctx.iter_installed_prefixes()
                if installed_prefix.name == environment
            ),
            None,
        )
        if ctx.envs_dir_identity() != envs_identity:
            raise CondaWorkspacesError(
                "Workspace environments directory changed while it was inspected."
            )

        publication = WorkspacePublication(
            ctx,
            manifest_path,
            original_text,
            updated_text,
            "environment removal",
        )
        publication_context = nullcontext() if dry_run else publication.guard()
        with publication_context:
            validate_lockfile_output(ctx, lockfile_path(ctx))
            rendered_lockfile = render_lockfile(
                ctx,
                resolve_all_environments(config),
                config=config,
                dry_run=dry_run,
            )

            escaped_name = status.escape_for_console(environment)
            manifest_entry = (
                "["
                + ".".join(
                    str(tomlkit.key(key))
                    for key in (*namespace, "environments", environment)
                )
                + "]"
            )
            action = "Would remove" if dry_run else "Removing"
            console.print(
                f"[bold cyan]{action}[/bold cyan] [bold]{escaped_name}[/bold]"
                " environment"
            )
            console.print(
                "Manifest entry: "
                f"[bold]{status.escape_for_console(manifest_entry)}[/bold] in "
                f"[bold]{status.escape_for_console(manifest_path.name)}[/bold]"
            )
            console.print(
                "Lock records: all platforms for "
                f"[bold]{escaped_name}[/bold] in [bold]conda.lock[/bold]"
            )
            prefix_state = (
                "installed" if prefix_identity is not None else "not installed"
            )
            console.print(
                f"Prefix: [bold]{status.escape_for_console(prefix)}[/bold]"
                f" ({prefix_state})"
            )

            if dry_run:
                return 0

            if prefix_identity is not None:
                if envs_identity is None:
                    raise CondaWorkspacesError(
                        "Workspace environments directory changed while it was"
                        " inspected."
                    )
                confirm_yn(
                    f"Remove {status.escape_for_console(environment)} environment?",
                    default="no",
                    dry_run=False,
                )
                publication.validate_manifest_generation()
                remove_environment(
                    ctx,
                    environment,
                    expected_envs_identity=envs_identity,
                    expected_prefix_identity=prefix_identity,
                )
            publication.publish_lockfile(rendered_lockfile)

        console.print(
            f"[bold cyan]Removed[/bold cyan] [bold]{escaped_name}[/bold] environment"
        )
        return 0

    if source is not None:
        reject_legacy_default_feature(source)
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
