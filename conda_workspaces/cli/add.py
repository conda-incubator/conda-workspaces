"""``conda workspace add`` — add dependencies to the workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit

from ..parsers import detect_workspace_file

if TYPE_CHECKING:
    import argparse


def execute_add(args: argparse.Namespace) -> int:
    """Add dependencies to the workspace manifest."""
    manifest_path = getattr(args, "file", None) or detect_workspace_file()
    specs = args.specs
    is_pypi = getattr(args, "pypi", False)
    feature = getattr(args, "feature", None)
    environment = getattr(args, "environment", None)

    # Determine which feature to target
    target_feature = feature or environment  # environment name == feature name

    text = manifest_path.read_text(encoding="utf-8")
    doc = tomlkit.loads(text)

    dep_key = "pypi-dependencies" if is_pypi else "dependencies"

    if manifest_path.name == "pyproject.toml":
        _add_to_pyproject(doc, specs, dep_key, target_feature)
    else:
        _add_to_toml(doc, specs, dep_key, target_feature)

    manifest_path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    label = "PyPI" if is_pypi else "conda"
    location = f"feature '{target_feature}'" if target_feature else "default"

    print(
        f"Added {len(specs)} {label} dependency(ies)"
        f" to {location} in {manifest_path.name}"
    )
    return 0


def _add_to_toml(
    doc: tomlkit.TOMLDocument,
    specs: list[str],
    dep_key: str,
    feature: str | None,
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
            print(f"Created environment '{feature}' with feature '{feature}'")
    else:
        target = doc

    deps = target.setdefault(dep_key, tomlkit.table())
    for spec in specs:
        name, _, version = spec.partition(" ")
        deps[name] = version.strip() if version.strip() else "*"


def _add_to_pyproject(
    doc: tomlkit.TOMLDocument,
    specs: list[str],
    dep_key: str,
    feature: str | None,
) -> None:
    """Add deps to a pyproject.toml with [tool.conda.*] / [tool.pixi.*] tables."""
    tool = doc.setdefault("tool", tomlkit.table())

    if "conda" in tool:
        source = tool["conda"]
    elif "conda-workspaces" in tool:
        source = tool["conda-workspaces"]
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
            print(f"Created environment '{feature}' with feature '{feature}'")
    else:
        target = source

    deps = target.setdefault(dep_key, tomlkit.table())
    for spec in specs:
        name, _, version = spec.partition(" ")
        deps[name] = version.strip() if version.strip() else "*"
