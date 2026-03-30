"""``conda workspace add`` — add dependencies to the workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit
from conda.models.match_spec import MatchSpec
from rich.console import Console

from ...parsers import detect_workspace_file

if TYPE_CHECKING:
    import argparse


def _parse_spec(spec: str) -> tuple[str, str]:
    """Extract package name and version constraint from a MatchSpec string.

    Uses conda's own MatchSpec parser (CEP 29) to handle all valid forms:
    ``python>=3.12``, ``python >=3.12``, ``python=3.12``, ``numpy``, etc.
    Returns ``"*"`` when no version constraint is present.
    """
    ms = MatchSpec(spec)
    version = str(ms.version) if ms.version else "*"
    return ms.name, version


def execute_add(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Add dependencies to the workspace manifest."""
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

    if manifest_path.name == "pyproject.toml":
        _add_to_pyproject(doc, specs, dep_key, target_feature, console=console)
    else:
        _add_to_toml(doc, specs, dep_key, target_feature, console=console)

    manifest_path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    label = "PyPI" if is_pypi else "conda"
    location = f"feature '{target_feature}'" if target_feature else "default"
    n = len(specs)
    noun = "dependency" if n == 1 else "dependencies"
    console.print(
        f"[bold cyan]Added[/bold cyan] {n} {label} {noun}"
        f" to {location} in [bold]{manifest_path.name}[/bold]"
    )
    return 0


def _add_to_toml(
    doc: tomlkit.TOMLDocument,
    specs: list[str],
    dep_key: str,
    feature: str | None,
    *,
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
            console.print(
                f"Created [bold]{feature}[/bold] environment"
                f" with feature '{feature}'"
            )
    else:
        target = doc

    deps = target.setdefault(dep_key, tomlkit.table())
    for spec in specs:
        name, version = _parse_spec(spec)
        deps[name] = version


def _add_to_pyproject(
    doc: tomlkit.TOMLDocument,
    specs: list[str],
    dep_key: str,
    feature: str | None,
    *,
    console: Console,
) -> None:
    """Add deps to a pyproject.toml with [tool.conda.*] / [tool.pixi.*] tables."""
    tool = doc.setdefault("tool", tomlkit.table())

    if "conda" in tool:
        source = tool["conda"]
    else:
        source = tool.setdefault("pixi", tomlkit.table())

    if feature:
        feat_table = source.setdefault("feature", tomlkit.table())
        target = feat_table.setdefault(feature, tomlkit.table())

        envs = source.setdefault("environments", tomlkit.table())
        if feature not in envs:
            entry = tomlkit.inline_table()
            entry["features"] = [feature]
            envs[feature] = entry
            console.print(
                f"Created [bold]{feature}[/bold] environment"
                f" with feature '{feature}'"
            )
    else:
        target = source

    deps = target.setdefault(dep_key, tomlkit.table())
    for spec in specs:
        name, version = _parse_spec(spec)
        deps[name] = version
