"""``conda workspace init`` — scaffold a new workspace manifest."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from rich.console import Console

from ...manifests.base import ManifestParser
from .. import status

if TYPE_CHECKING:
    import argparse


def execute_init(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Create a new workspace manifest in the current directory.

    Delegates the actual file layout to
    :meth:`ManifestParser.write_workspace_stub` on the parser selected
    by ``--format``.  The default implementation writes a fresh
    ``conda.toml`` / ``pixi.toml``; :class:`PyprojectTomlParser`
    overrides it to append ``[tool.conda]`` to an existing
    ``pyproject.toml`` when one is present.  ``init`` itself stays
    format-agnostic and only owns argument wiring and the user-visible
    status message.
    """
    if console is None:
        console = Console(highlight=False)

    name = args.name or Path.cwd().name
    channels = args.channels or ["conda-forge"]
    platforms = args.platforms or [conda_context.subdir]
    base_dir = Path(args.file).parent if args.file else Path.cwd()

    parser = ManifestParser.for_format_alias(args.manifest_format)
    path, verb = parser.write_workspace_stub(base_dir, name, channels, platforms)
    status.message(console, verb, "workspace", path.name, detail=str(path.parent))
    return 0
