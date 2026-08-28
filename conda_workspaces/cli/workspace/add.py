"""``conda workspace add`` — add dependencies or an environment."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from conda.exceptions import InvalidMatchSpec
from conda.models.match_spec import MatchSpec
from conda.utils import quote_for_shell
from rich.console import Console
from tomlkit.items import InlineTable, Table

from ...context import WorkspaceContext
from ...exceptions import CondaWorkspacesError, FeatureNotFoundError
from ...manifests import detect_workspace_file, find_parser
from ...manifests.toml import WorkspaceDependencyResolver
from ...models import Environment
from ...publication import WorkspacePublication
from ...resolver import resolve_environment
from .. import status
from . import workspace_manifest_path_from_args
from .dependencies import (
    DependencyLocation,
    effective_dependency_location,
    ensure_child_table,
    reject_legacy_default_feature,
    workspace_toml_source,
)
from .sync import affected_environments, sync_environments

if TYPE_CHECKING:
    import argparse


def execute_add(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Add dependencies or declare a new workspace environment."""
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
    with_features = list(dict.fromkeys(getattr(args, "with_feature", []) or []))
    no_default_feature = getattr(args, "no_default_feature", False)
    location = DependencyLocation.from_selectors(
        feature=feature,
        environment=environment,
        platform=platform,
    )

    parser = find_parser(manifest_path)
    original_text = parser.read_manifest_text(manifest_path)
    doc = parser.parse_toml_text_with_redacted_errors(original_text, manifest_path)
    dep_key = "pypi-dependencies" if is_pypi else "dependencies"

    # Quickstart keeps prospective TOML staged while validating real outputs.
    validation_manifest_path = (
        getattr(args, "validation_manifest_path", None) or manifest_path
    )
    source, namespace = workspace_toml_source(doc, manifest_path, create=True)
    assert source is not None
    reject_legacy_default_feature(source)
    current = parser.parse_data_with_redacted_errors(
        doc.unwrap(), validation_manifest_path
    )
    creating_environment = (
        environment is not None and environment not in current.environments
    )
    if not specs:
        if environment is None:
            raise CondaWorkspacesError(
                "Package specs are required unless a new environment is selected.",
                hints=["Pass '-e NAME' to declare a new environment."],
            )
        if not creating_environment:
            command = ["conda", "workspace"]
            if selected_manifest_path is not None:
                command.extend(("--file", str(selected_manifest_path)))
            command.extend(("install", "-e", environment))
            raise CondaWorkspacesError(
                f"Environment '{environment}' is already defined in the workspace.",
                hints=[f"Run '{quote_for_shell(*command)}' to synchronize it."],
            )
        if is_pypi or platform is not None:
            options = []
            if is_pypi:
                options.append("--pypi")
            if platform is not None:
                options.append("--platform")
            raise CondaWorkspacesError(
                f"{' and '.join(options)} can only be used when adding package specs."
            )
    if (with_features or no_default_feature) and environment is None:
        raise CondaWorkspacesError(
            "--with-feature and --no-default-feature require --environment."
        )
    if (with_features or no_default_feature) and not creating_environment:
        raise CondaWorkspacesError(
            "--with-feature and --no-default-feature can only be used when"
            " declaring a new environment."
        )
    for feature_name in with_features:
        if feature_name not in current.features:
            assert environment is not None
            raise FeatureNotFoundError(feature_name, environment)
    location.validate_platform(current, source)
    added_names = _add_to_toml(
        source,
        specs,
        dep_key,
        location,
        with_features=with_features,
        no_default_feature=no_default_feature,
        dry_run=dry_run,
        console=console,
    )
    parser.validate_no_url_credentials(
        doc.unwrap(),
        manifest_path,
        content=tomlkit.dumps(doc),
    )

    config = parser.parse_data_with_redacted_errors(
        doc.unwrap(),
        validation_manifest_path,
    )
    updated_text = tomlkit.dumps(doc)

    env_names = affected_environments(
        config,
        location.feature,
        target_environment=location.environment,
    )
    warnings: dict[tuple[DependencyLocation, str], list[str]] = {}
    for env_name in env_names:
        env = config.environments[env_name]
        for configured_platform in resolve_environment(config, env_name).platforms:
            if (
                location.platform is not None
                and location.platform
                not in config.target_platform_keys(configured_platform)
            ):
                continue
            for name in added_names:
                winner = effective_dependency_location(
                    source,
                    config,
                    env,
                    configured_platform,
                    dep_key,
                    name,
                )
                if winner is not None and winner != location:
                    warnings.setdefault((winner, name), []).append(
                        f"{env_name}/{configured_platform}"
                    )
    ctx = WorkspaceContext(config)
    publication = WorkspacePublication(
        ctx,
        manifest_path,
        original_text,
        updated_text,
        "add",
    )
    publication_context = nullcontext() if dry_run else publication.guard()
    with publication_context:
        if specs:
            label = "PyPI" if is_pypi else "conda"
            n = len(specs)
            noun = "dependency" if n == 1 else "dependencies"
            action = "Would add" if dry_run else "Added"
            console.print(
                f"[bold cyan]{action}[/bold cyan] {n} {label} {noun}"
                f" to {status.escape_for_console(location.display_name)} in [bold]"
                f"{status.escape_for_console(manifest_path.name)}[/bold]"
            )
        warning_console = (
            Console(stderr=True, highlight=False)
            if getattr(args, "json", False)
            else console
        )
        for (winner, name), contexts in warnings.items():
            warning = (
                f"Warning: '{name}' in {winner.table_name(dep_key, namespace)}"
                f" overrides the selected {location.table_name(dep_key, namespace)}"
                f" for {', '.join(contexts)}. Rerun using {winner.selector()} as"
                " the complete location selector."
            )
            warning_console.print(
                status.escape_for_console(warning),
                style="yellow",
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
                publish_lockfile=publication.publish_lockfile,
                console=console,
            )
        elif not dry_run:
            publication.publish_manifest()
    return 0


def _add_to_toml(
    doc: tomlkit.TOMLDocument | Table | InlineTable,
    specs: list[str],
    dep_key: str,
    location: DependencyLocation,
    *,
    with_features: list[str],
    no_default_feature: bool,
    dry_run: bool,
    console: Console,
) -> list[str]:
    """Add deps to a pixi.toml or conda.toml document."""
    environments = doc.get("environments")
    if (
        location.environment not in (None, Environment.DEFAULT_NAME)
        and not environments
    ):
        ensure_child_table(doc, "environments")[Environment.DEFAULT_NAME] = []
    target, created_environment = location.ensure_table(doc)
    if location.feature is not None:
        envs = ensure_child_table(doc, "environments")
        if location.feature not in envs:
            entry = tomlkit.inline_table()
            entry["features"] = [location.feature]
            envs[location.feature] = entry
            action = "Would create" if dry_run else "Created"
            console.print(
                f"[bold cyan]{action}[/bold cyan]"
                f" [bold]{location.feature}[/bold] environment"
            )
    elif created_environment:
        assert location.environment is not None
        environment = ensure_child_table(doc, "environments")[location.environment]
        assert isinstance(environment, (Table, InlineTable))
        if with_features:
            environment["features"] = with_features
        if no_default_feature:
            environment["no-default-feature"] = True
        action = "Would create" if dry_run else "Created"
        console.print(
            f"[bold cyan]{action}[/bold cyan]"
            f" [bold]{location.environment}[/bold] environment"
        )

    if not specs:
        return []

    deps = target.get(dep_key)
    if deps is None:
        deps = (
            tomlkit.inline_table()
            if isinstance(target, InlineTable)
            else tomlkit.table()
        )
        target[dep_key] = deps
    added: list[str] = []
    for raw_spec in specs:
        spec = MatchSpec(raw_spec)
        name = spec.get_exact_value("name")
        if not name:
            raise InvalidMatchSpec(raw_spec, "an exact package name is required")
        if name not in added:
            added.append(name)
        if dep_key == "dependencies":
            value = WorkspaceDependencyResolver.match_spec_to_toml(spec)
            if spec.is_name_only_spec and name in deps:
                continue
            deps[name] = value
        else:
            deps[name] = str(spec.version) if spec.version else "*"
    return added
