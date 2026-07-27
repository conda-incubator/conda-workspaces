"""``conda workspace remove`` — remove dependencies from the manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit
from rich.console import Console

from ...context import WorkspaceContext
from ...exceptions import CondaWorkspacesError
from ...manifests import detect_workspace_file, find_parser
from ...models import Feature
from . import workspace_manifest_path_from_args
from .sync import affected_environments, sync_environments

if TYPE_CHECKING:
    import argparse

    from tomlkit.items import Table


def execute_remove(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Remove dependencies from the workspace manifest."""
    if console is None:
        console = Console(highlight=False)
    manifest_path = workspace_manifest_path_from_args(args) or detect_workspace_file()
    specs = args.specs
    is_pypi = getattr(args, "pypi", False)
    feature = getattr(args, "feature", None)
    if feature == Feature.DEFAULT_NAME:
        feature = None
    environment = getattr(args, "environment", None)
    dry_run = getattr(args, "dry_run", False)

    text = manifest_path.read_text(encoding="utf-8")
    doc = tomlkit.loads(text)
    dep_key = "pypi-dependencies" if is_pypi else "dependencies"

    source: tomlkit.TOMLDocument | Table | None = doc
    if manifest_path.name == "pyproject.toml":
        tool = doc.get("tool", {})
        conda = tool.get("conda")
        if conda is not None and "workspace" in conda:
            source = conda
        else:
            source = tool.get("pixi")
    if environment and source is not None:
        current = find_parser(manifest_path).parse_data(doc.unwrap(), manifest_path)
        env = current.get_environment(environment)
        local = env.pypi_dependencies if is_pypi else env.conda_dependencies
        for name in specs:
            if name in local:
                continue
            inherited_from = [
                inherited_feature
                for inherited_feature in current.resolve_features(env)
                if name
                in (
                    inherited_feature.pypi_dependencies
                    if is_pypi
                    else inherited_feature.conda_dependencies
                )
            ]
            if inherited_from:
                source_names = ", ".join(
                    "the default feature"
                    if inherited_feature.is_default
                    else f"feature '{inherited_feature.name}'"
                    for inherited_feature in inherited_from
                )
                pypi = "--pypi " if is_pypi else ""
                commands = [
                    (
                        f"conda workspace remove {pypi}{name}"
                        if inherited_feature.is_default
                        else (
                            "conda workspace remove "
                            f"{pypi}--feature {inherited_feature.name} {name}"
                        )
                    )
                    for inherited_feature in inherited_from
                ]
                raise CondaWorkspacesError(
                    f"Dependency '{name}' is not declared directly on"
                    f" environment '{environment}'.",
                    hints=[
                        f"It is inherited from {source_names}.",
                        *[f"Run '{command}'." for command in commands],
                    ],
                )
    removed = (
        _remove_from_toml(source, specs, dep_key, feature, environment)
        if source is not None
        else []
    )

    if removed:
        config = find_parser(manifest_path).parse_data(doc.unwrap(), manifest_path)
        if not dry_run:
            manifest_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        label = "PyPI" if is_pypi else "conda"
        if environment:
            location = f"environment '{environment}'"
        elif feature:
            location = f"feature '{feature}'"
        else:
            location = "default"
        n = len(removed)
        noun = "dependency" if n == 1 else "dependencies"
        action = "Would remove" if dry_run else "Removed"
        console.print(
            f"[bold cyan]{action}[/bold cyan] {n} {label} {noun}"
            f" from {location} in [bold]{manifest_path.name}[/bold]"
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
        feature,
        target_environment=environment,
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
            console=console,
        )
    return 0


def _remove_from_toml(
    doc: tomlkit.TOMLDocument | Table,
    specs: list[str],
    dep_key: str,
    feature: str | None,
    environment: str | None,
) -> list[str]:
    """Remove deps from a pixi.toml or conda.toml document."""
    if feature:
        feat_table = doc.get("feature", {})
        target = feat_table.get(feature, {})
    elif environment:
        envs = doc.get("environments", {})
        definition = envs.get(environment, {})
        target = {} if isinstance(definition, list) else definition
    else:
        target = doc

    deps = target.get(dep_key, {})
    removed: list[str] = []
    for name in specs:
        if name in deps:
            del deps[name]
            removed.append(name)
    return removed
