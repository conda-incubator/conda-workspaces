"""``conda workspace add`` — add dependencies to the workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit
from conda.exceptions import InvalidMatchSpec
from conda.models.match_spec import MatchSpec
from rich.console import Console

from ...context import WorkspaceContext
from ...manifests import detect_workspace_file, find_parser
from ...manifests.toml import WorkspaceDependencyResolver
from . import workspace_manifest_path_from_args
from .sync import affected_environments, sync_environments

if TYPE_CHECKING:
    import argparse

    from tomlkit.items import Table


def execute_add(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Add dependencies to the workspace manifest."""
    if console is None:
        console = Console(highlight=False)
    manifest_path = workspace_manifest_path_from_args(args) or detect_workspace_file()
    specs = args.specs
    is_pypi = getattr(args, "pypi", False)
    feature = getattr(args, "feature", None)
    environment = getattr(args, "environment", None)
    target_feature = feature or environment
    dry_run = getattr(args, "dry_run", False)

    text = manifest_path.read_text(encoding="utf-8")
    doc = tomlkit.loads(text)
    dep_key = "pypi-dependencies" if is_pypi else "dependencies"

    source: tomlkit.TOMLDocument | Table = doc
    if manifest_path.name == "pyproject.toml":
        tool = doc.setdefault("tool", tomlkit.table())
        conda = tool.get("conda")
        source = (
            conda
            if conda is not None and "workspace" in conda
            else tool.setdefault("pixi", tomlkit.table())
        )
    _add_to_toml(
        source,
        specs,
        dep_key,
        target_feature,
        dry_run=dry_run,
        console=console,
    )

    # Quickstart keeps prospective TOML staged while validating real outputs.
    validation_manifest_path = (
        getattr(args, "validation_manifest_path", None) or manifest_path
    )
    config = find_parser(manifest_path).parse_data(
        doc.unwrap(),
        validation_manifest_path,
    )
    if not dry_run:
        manifest_path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    label = "PyPI" if is_pypi else "conda"
    location = f"feature '{target_feature}'" if target_feature else "default"
    n = len(specs)
    noun = "dependency" if n == 1 else "dependencies"
    action = "Would add" if dry_run else "Added"
    console.print(
        f"[bold cyan]{action}[/bold cyan] {n} {label} {noun}"
        f" to {location} in [bold]{manifest_path.name}[/bold]"
    )

    if getattr(args, "no_lockfile_update", False):
        return 0

    ctx = WorkspaceContext(config)
    env_names = affected_environments(config, target_feature)
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


def _add_to_toml(
    doc: tomlkit.TOMLDocument | Table,
    specs: list[str],
    dep_key: str,
    feature: str | None,
    *,
    dry_run: bool,
    console: Console,
) -> None:
    """Add deps to a pixi.toml or conda.toml document."""
    if feature:
        feat_table = doc.setdefault("feature", tomlkit.table())
        target = feat_table.setdefault(feature, tomlkit.table())

        envs = doc.setdefault("environments", tomlkit.table())
        if feature not in envs:
            entry = tomlkit.inline_table()
            entry["features"] = [feature]
            envs[feature] = entry
            action = "Would create" if dry_run else "Created"
            console.print(
                f"[bold cyan]{action}[/bold cyan] [bold]{feature}[/bold] environment"
            )
    else:
        target = doc

    deps = target.setdefault(dep_key, tomlkit.table())
    for raw_spec in specs:
        spec = MatchSpec(raw_spec)
        name = spec.get_exact_value("name")
        if not name:
            raise InvalidMatchSpec(raw_spec, "an exact package name is required")
        if dep_key == "dependencies":
            value = WorkspaceDependencyResolver.match_spec_to_toml(spec)
            if spec.is_name_only_spec and name in deps:
                continue
            deps[name] = value
        else:
            deps[name] = str(spec.version) if spec.version else "*"
