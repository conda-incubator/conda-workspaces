"""``conda workspace init`` — scaffold a new workspace manifest."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from conda.base.context import context as conda_context

from ..exceptions import ManifestExistsError

if TYPE_CHECKING:
    import argparse


def execute_init(args: argparse.Namespace) -> int:
    """Create a new workspace manifest in the current directory."""
    fmt = args.manifest_format
    name = args.name or Path.cwd().name
    channels = args.channels or ["conda-forge"]

    if args.platforms:
        platforms = args.platforms
    else:
        platforms = _detect_platforms()

    base_dir = Path(args.file).parent if args.file else Path.cwd()

    if fmt == "pixi":
        return _write_workspace_toml("pixi.toml", name, channels, platforms, base_dir)
    elif fmt == "conda":
        return _write_workspace_toml("conda.toml", name, channels, platforms, base_dir)
    elif fmt == "pyproject":
        return _write_pyproject_toml(name, channels, platforms, base_dir)

    return 1


def _detect_platforms() -> list[str]:
    """Return the current platform as detected by conda."""
    return [conda_context.subdir]


def _write_workspace_toml(
    filename: str,
    name: str,
    channels: list[str],
    platforms: list[str],
    base_dir: Path,
) -> int:
    path = base_dir / filename
    if path.exists():
        raise ManifestExistsError(path)

    doc = tomlkit.document()

    ws = tomlkit.table()
    ws.add("name", name)
    ws.add("channels", channels)
    ws.add("platforms", platforms)
    doc.add("workspace", ws)

    deps = tomlkit.table()
    doc.add("dependencies", deps)

    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    print(f"Created {path}")
    return 0


def _write_pyproject_toml(
    name: str,
    channels: list[str],
    platforms: list[str],
    base_dir: Path,
) -> int:
    path = base_dir / "pyproject.toml"
    existed = path.exists()

    if existed:
        text = path.read_text(encoding="utf-8")
        doc = tomlkit.loads(text)
    else:
        doc = tomlkit.document()

    tool = doc.setdefault("tool", tomlkit.table())
    if "conda" in tool:
        raise ManifestExistsError("[tool.conda] in pyproject.toml")
    if "pixi" in tool:
        raise ManifestExistsError("[tool.pixi] in pyproject.toml")

    conda = tomlkit.table()
    ws = tomlkit.table()
    ws.add("name", name)
    ws.add("channels", channels)
    ws.add("platforms", platforms)
    conda.add("workspace", ws)
    conda.add("dependencies", tomlkit.table())
    tool.add("conda", conda)

    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    print(f"{'Updated' if existed else 'Created'} {path}")
    return 0
