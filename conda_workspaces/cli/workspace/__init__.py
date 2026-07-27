"""CLI subpackage for ``conda workspace`` subcommands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conda.exceptions import CondaValueError

from ...context import WorkspaceContext
from ...manifests import detect_and_parse
from ...paths import validate_path_parent

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from ...models import WorkspaceConfig


def workspace_manifest_path_from_args(
    args: argparse.Namespace,
    *,
    for_mutation: bool = False,
) -> Path | None:
    """Return the exact manifest selected by the global ``--file`` option."""
    path = args.manifest_file
    if path is not None and not path.is_file():
        raise CondaValueError(
            f"--file must name an existing workspace manifest file: {path}"
        )
    if path is None:
        return None
    path = path.absolute()
    if for_mutation:
        validate_path_parent(path)
    return path


def workspace_context_from_args(
    args: argparse.Namespace,
    *,
    for_mutation: bool = False,
) -> tuple[WorkspaceConfig, WorkspaceContext]:
    """Parse the workspace manifest and build a context from CLI *args*.

    Uses ``--file`` / ``-f`` when provided, otherwise auto-detects. Quickstart
    previews may parse a staged file while validating paths at
    ``validation_manifest_path``.
    """
    _, config = detect_and_parse(
        workspace_manifest_path_from_args(args, for_mutation=for_mutation)
    )
    validation_manifest_path = getattr(args, "validation_manifest_path", None)
    if validation_manifest_path is not None:
        config.root = str(validation_manifest_path.parent)
        config.manifest_path = str(validation_manifest_path)
    return config, WorkspaceContext(config)
