"""``conda workspace update`` — selectively update declared conda roots."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from conda.exceptions import InvalidMatchSpec
from conda.models.match_spec import MatchSpec
from conda.utils import quote_for_shell
from rich.console import Console

from ...context import WorkspaceContext
from ...exceptions import (
    CondaWorkspacesError,
    EnvironmentNotInstalledError,
    PlatformError,
)
from ...lockfile import (
    CondaLockLoader,
    check_lockfile_satisfiability,
    load_lockfile_data,
    lockfile_path,
    render_lockfile,
)
from ...manifests import detect_workspace_file, find_parser
from ...manifests.toml import WorkspaceDependencyResolver
from ...models import LockfileStatus
from ...paths import validate_file_output
from ...publication import WorkspacePublication
from ...resolver import resolve_all_environments
from . import workspace_manifest_path_from_args
from .dependencies import (
    DependencyLocation,
    dependency_declarations,
    effective_dependency_location,
    reject_legacy_default_feature,
    workspace_toml_source,
)
from .sync import sync_environments

if TYPE_CHECKING:
    import argparse


def execute_update(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Update selected declared roots without widening their constraints."""
    if console is None:
        console = Console(highlight=False)
    dry_run = getattr(args, "dry_run", False)
    no_install = getattr(args, "no_install", False)
    requested_manifest_path = getattr(args, "manifest_file", None)
    if (
        requested_manifest_path is not None
        and Path(requested_manifest_path).is_symlink()
    ):
        WorkspacePublication.validate_manifest_path(Path(requested_manifest_path))
    selected_manifest_path = workspace_manifest_path_from_args(
        args,
        for_mutation=True,
    )
    manifest_path = selected_manifest_path or detect_workspace_file()
    if manifest_path.is_symlink():
        WorkspacePublication.validate_manifest_path(manifest_path)
    location = DependencyLocation.from_selectors(
        feature=getattr(args, "feature", None),
        environment=getattr(args, "environment", None),
        platform=getattr(args, "platform", None),
    )

    parser = find_parser(manifest_path)
    original_text = parser.read_manifest_text(manifest_path)
    document = parser.parse_toml_text_with_redacted_errors(
        original_text,
        manifest_path,
    )
    source, namespace = workspace_toml_source(
        document,
        manifest_path,
        create=False,
    )
    if source is None:
        raise CondaWorkspacesError(
            f"No workspace dependency tables found in '{manifest_path}'."
        )

    reject_legacy_default_feature(source)
    current = parser.parse_data_with_redacted_errors(document.unwrap(), manifest_path)
    if location.environment is not None:
        current.get_environment(location.environment)
    location.validate_platform(current, source, allow_existing=True)

    selected = location.find_table(source)
    selected_dependencies = (
        selected.get("dependencies", {}) if selected is not None else {}
    )
    parsed_specs: dict[str, MatchSpec] = {}
    for raw_spec in args.specs:
        spec = MatchSpec(raw_spec)
        name = spec.get_exact_value("name")
        if not name:
            raise InvalidMatchSpec(raw_spec, "an exact package name is required")
        if name in parsed_specs:
            raise CondaWorkspacesError(
                f"Package '{name}' was requested more than once."
            )
        parsed_specs[name] = spec

    missing = [name for name in parsed_specs if name not in selected_dependencies]
    if missing:
        hints: list[str] = []
        for name in missing:
            for declaration in dependency_declarations(source, name):
                if declaration.pypi:
                    hints.append(
                        f"'{name}' is a PyPI dependency, which workspace update"
                        " does not manage."
                    )
                    continue
                hints.append(
                    "Run '"
                    + declaration.location.command(
                        "update",
                        str(parsed_specs[name]),
                        pypi=False,
                        manifest_path=selected_manifest_path,
                    )
                    + "'."
                )
        names = ", ".join(f"'{name}'" for name in missing)
        noun = "dependency" if len(missing) == 1 else "dependencies"
        verb = "is" if len(missing) == 1 else "are"
        raise CondaWorkspacesError(
            f"Conda {noun} {names} {verb} not declared directly in"
            f" {location.table_name('dependencies', namespace)}.",
            hints=hints,
        )

    ctx = WorkspaceContext(current)
    resolved_current = resolve_all_environments(current)
    update_targets: dict[tuple[str, str], set[str]] = {}
    effective_names: set[str] = set()
    for env_name, environment in current.environments.items():
        resolved = resolved_current[env_name]
        for platform in resolved.platforms or [ctx.platform]:
            names = {
                name
                for name in parsed_specs
                if effective_dependency_location(
                    source,
                    current,
                    environment,
                    platform,
                    "dependencies",
                    name,
                )
                == location
            }
            if names:
                update_targets[(env_name, platform)] = names
                effective_names.update(names)

    shadowed = parsed_specs.keys() - effective_names
    if shadowed:
        names = ", ".join(f"'{name}'" for name in sorted(shadowed))
        raise CondaWorkspacesError(
            f"Selected declarations for {names} are overridden in every"
            " environment and platform.",
            hints=[
                (
                    "Choose the effective declaration with --feature,"
                    " --environment, and --platform."
                )
            ],
        )

    installed_envs: list[str] = []
    if not no_install:
        for env_name, resolved in resolved_current.items():
            try:
                host_platform = resolved.resolve_platform_name(ctx.platform)
            except PlatformError:
                continue
            update_names = update_targets.get((env_name, host_platform))
            if not update_names or not ctx.env_exists(env_name):
                continue
            installed_envs.append(env_name)
        if not installed_envs:
            if location.environment is not None and not ctx.env_exists(
                location.environment
            ):
                raise EnvironmentNotInstalledError(location.environment)
            raise CondaWorkspacesError(
                "No installed workspace environment uses the selected"
                " dependency declaration on this platform.",
                hints=[
                    (
                        "Install an affected environment first, or pass --no-install"
                        " to update only conda.lock."
                    )
                ],
            )

    for name, spec in parsed_specs.items():
        if spec.is_name_only_spec:
            continue
        selected_dependencies[name] = WorkspaceDependencyResolver.match_spec_to_toml(
            spec
        )

    parser.validate_no_url_credentials(
        document.unwrap(),
        manifest_path,
        content=tomlkit.dumps(document),
    )
    config = parser.parse_data_with_redacted_errors(document.unwrap(), manifest_path)
    ctx = WorkspaceContext(config)

    updated_text = tomlkit.dumps(document)
    if not dry_run and updated_text != original_text:
        validate_file_output(manifest_path)

    count = len(parsed_specs)
    noun = "dependency" if count == 1 else "dependencies"
    action = "Would update" if dry_run else "Updating"
    console.print(
        f"[bold cyan]{action}[/bold cyan] {count} conda {noun}"
        f" from {location.display_name} in [bold]{manifest_path.name}[/bold]"
    )
    console.print()

    publication = WorkspacePublication(
        ctx,
        manifest_path,
        original_text,
        updated_text,
        "update",
    )

    publication_guard = nullcontext() if dry_run else publication.guard()
    try:
        with publication_guard:
            baseline_data = None
            baseline_path = lockfile_path(ctx)
            if baseline_path.is_file():
                candidate = load_lockfile_data(publication.read_lockfile_bytes())
                lock_environments = candidate.get("environments", {})
                platforms = {
                    platform
                    for resolved in resolved_current.values()
                    for platform in (resolved.platforms or [ctx.platform])
                }
                statuses = [
                    check_lockfile_satisfiability(current, candidate, platform)
                    for platform in platforms
                ]
                complete = set(lock_environments) == set(current.environments) and all(
                    set(lock_environments[name].get("packages", {}))
                    == set(resolved_current[name].platforms or [ctx.platform])
                    for name in current.environments
                )
                complete = complete and all(
                    status.status == LockfileStatus.UP_TO_DATE for status in statuses
                )
                if complete:
                    try:
                        for (env_name, platform), names in update_targets.items():
                            records = CondaLockLoader.package_records_for_env_data(
                                candidate,
                                env_name,
                                platform,
                            )
                            if names - {record.name for record in records}:
                                complete = False
                                break
                    except ValueError:
                        complete = False
                if complete:
                    baseline_data = candidate

            if baseline_data is None:
                baseline_data = load_lockfile_data(
                    render_lockfile(
                        ctx,
                        resolved_current,
                        config=current,
                        dry_run=dry_run,
                    )
                )

            sync_environments(
                config,
                ctx,
                installed_envs,
                no_install=no_install,
                dry_run=dry_run,
                baseline_lockfile=baseline_data,
                update_targets=update_targets,
                publish_lockfile=publication.publish_lockfile,
                validate_workspace=publication.validate_manifest_generation,
                console=console,
            )
    except Exception as exc:
        if not publication.started:
            raise
        recovery_prefix = ["conda", "workspace"]
        if selected_manifest_path is not None:
            recovery_prefix.extend(("--file", str(selected_manifest_path)))
        if installed_envs:
            hints = [
                "Run '"
                + quote_for_shell(
                    *recovery_prefix,
                    "install",
                    "-e",
                    env_name,
                )
                + "' to reconcile this affected installed environment."
                for env_name in installed_envs
            ]
        else:
            hints = [
                "Run '"
                + quote_for_shell(*recovery_prefix, "lock")
                + "' to refresh conda.lock from the published manifest."
            ]
        raise CondaWorkspacesError(
            "Workspace update did not finish after publishing its desired"
            f" state: {exc}",
            hints=hints,
        ) from exc
    return 0
