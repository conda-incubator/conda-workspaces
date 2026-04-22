"""``conda workspace remove`` — remove dependencies from the manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit
from rich.console import Console

from ...parsers import detect_workspace_file
from . import workspace_context_from_args
from .sync import affected_environments, sync_environments

if TYPE_CHECKING:
    import argparse


def execute_remove(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Remove dependencies from the workspace manifest."""
    if console is None:
        console = Console(highlight=False)
    manifest_path = getattr(args, "file", None) or detect_workspace_file()
    specs = args.specs
    is_pypi = getattr(args, "pypi", False)
    feature = getattr(args, "feature", None)
    environment = getattr(args, "environment", None)
    target_feature = feature or environment

    text = manifest_path.read_text(encoding="utf-8")
    doc = tomlkit.loads(text)
    dep_key = "pypi-dependencies" if is_pypi else "dependencies"
    removed: list[str] = []

    if manifest_path.name == "pyproject.toml":
        removed = _remove_from_pyproject(doc, specs, dep_key, target_feature)
    else:
        removed = _remove_from_toml(doc, specs, dep_key, target_feature)

    if removed:
        manifest_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        label = "PyPI" if is_pypi else "conda"
        location = f"feature '{target_feature}'" if target_feature else "default"
        n = len(removed)
        noun = "dependency" if n == 1 else "dependencies"
        console.print(
            f"[bold cyan]Removed[/bold cyan] {n} {label} {noun}"
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

    config, ctx = workspace_context_from_args(args)
    env_names = affected_environments(config, target_feature)
    if env_names:
        console.print()
        sync_environments(
            config,
            ctx,
            env_names,
            no_install=getattr(args, "no_install", False),
            force_reinstall=getattr(args, "force_reinstall", False),
            dry_run=getattr(args, "dry_run", False),
            console=console,
        )
    return 0


def _remove_from_toml(
    doc: tomlkit.TOMLDocument,
    specs: list[str],
    dep_key: str,
    feature: str | None,
) -> list[str]:
    """Remove deps from a pixi.toml or conda.toml document."""
    if feature:
        feat_table = doc.get("feature", {})
        target = feat_table.get(feature, {})
    else:
        target = doc

    deps = target.get(dep_key, {})
    removed: list[str] = []
    for name in specs:
        if name in deps:
            del deps[name]
            removed.append(name)
    return removed


def _remove_from_pyproject(
    doc: tomlkit.TOMLDocument,
    specs: list[str],
    dep_key: str,
    feature: str | None,
) -> list[str]:
    """Remove deps from a pyproject.toml with [tool.conda.*] / [tool.pixi.*] tables."""
    tool = doc.get("tool", {})

    if "conda" in tool:
        source = tool["conda"]
    elif "pixi" in tool:
        source = tool["pixi"]
    else:
        return []

    if feature:
        feat_table = source.get("feature", {})
        target = feat_table.get(feature, {})
    else:
        target = source

    deps = target.get(dep_key, {})
    removed: list[str] = []
    for name in specs:
        if name in deps:
            del deps[name]
            removed.append(name)
    return removed
