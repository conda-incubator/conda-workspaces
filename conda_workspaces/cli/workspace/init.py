"""``conda workspace init`` — scaffold a new workspace manifest."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.exceptions import CondaValueError
from rich.console import Console

from ...manifests.base import ManifestParser
from ...models import redact_channel_name
from .. import status
from . import workspace_manifest_path_from_args

if TYPE_CHECKING:
    import argparse


def resolve_init_channels() -> list[str]:
    """Return conda's resolved channel order for a new workspace."""
    channels = [redact_channel_name(channel) for channel in conda_context.channels]
    if not channels:
        raise CondaValueError(
            "No channels are configured. Pass -c/--channel or configure one with"
            " 'conda config --append channels <channel>'."
        )
    return channels


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
    channels = resolve_init_channels()
    platforms = args.platforms or [conda_context.subdir]
    manifest_path = workspace_manifest_path_from_args(args)
    base_dir = manifest_path.parent if manifest_path else Path.cwd()

    parser = ManifestParser.for_format_alias(args.manifest_format)
    path, verb = parser.write_workspace_stub(base_dir, name, channels, platforms)
    # ``init`` has no structured output of its own, but a caller that
    # passes ``--json`` (accepted silently by ``_accept_json_silently``
    # in ``cli/main.py``) is piping stdout through a JSON parser — the
    # Rich status line would corrupt that. See the "--json contract"
    # section in ``AGENTS.md``.
    if not conda_context.json:
        status.message(console, verb, "workspace", path.name, detail=str(path.parent))
    return 0
