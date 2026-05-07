"""Manifest detection and parser registry (workspaces and tasks).

Search order (same for workspaces and tasks)
--------------------------------------------
1. ``conda.toml``     -- conda-native manifest format
2. ``pixi.toml``      -- pixi-native format (compatibility)
3. ``pyproject.toml``  -- pixi or conda tables embedded

The first file that exists *and* contains the relevant configuration wins.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ..exceptions import (
    NoTaskFileError,
    WorkspaceNotFoundError,
    WorkspaceParseError,
)
from .pixi_toml import PixiTomlParser
from .pyproject_toml import PyprojectTomlParser
from .toml import CondaTomlParser

if TYPE_CHECKING:
    from ..models import Task, WorkspaceConfig
    from .base import ManifestParser

_PARSERS: list[ManifestParser] = [
    CondaTomlParser(),
    PixiTomlParser(),
    PyprojectTomlParser(),
]

_SEARCH_FILES: list[str] = [
    "conda.toml",
    "pixi.toml",
    "pyproject.toml",
]

PARSER_BY_FILENAME: dict[str, ManifestParser] = {
    fname: parser for parser in _PARSERS for fname in parser.filenames
}


def _walk_manifests(
    start_dir: Path,
    predicate: str,
) -> Path | None:
    """Walk up from *start_dir* looking for a manifest matching *predicate*.

    *predicate* is a method name on ``ManifestParser`` — either
    ``"has_workspace"`` or ``"has_tasks"``.  Returns the first
    matching file path, or ``None`` if none is found.
    """
    current = start_dir.resolve()
    while True:
        for fname in _SEARCH_FILES:
            candidate = current / fname
            if candidate.is_file():
                parser = PARSER_BY_FILENAME.get(fname)
                if parser is not None and getattr(parser, predicate)(candidate):
                    return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def detect_workspace_file(
    start_dir: str | Path | None = None,
) -> Path:
    """Walk up from *start_dir* to find a workspace manifest.

    Returns the path to the first matching file.
    Raises ``WorkspaceNotFoundError`` if none is found.
    """
    if start_dir is None:
        start_dir = Path.cwd()
    else:
        start_dir = Path(start_dir)

    result = _walk_manifests(start_dir, "has_workspace")
    if result is None:
        raise WorkspaceNotFoundError(start_dir)
    return result


def find_parser(path: Path) -> ManifestParser:
    """Return the parser that can handle *path*.

    Raises ``WorkspaceParseError`` if no parser matches.
    """
    for parser in _PARSERS:
        if parser.can_handle(path):
            return parser
    raise WorkspaceParseError(path, f"No parser available for '{path.name}'")


@lru_cache(maxsize=4)
def _cached_parse(path_str: str) -> WorkspaceConfig:
    """LRU-cached wrapper around parser dispatch."""
    path = Path(path_str)
    parser = find_parser(path)
    return parser.parse(path)


def detect_and_parse(
    start_dir: str | Path | None = None,
) -> tuple[Path, WorkspaceConfig]:
    """Detect the workspace manifest and parse it.

    Returns ``(manifest_path, workspace_config)``.
    """
    path = detect_workspace_file(start_dir)
    config = _cached_parse(str(path))
    return path, config


def detect_task_file(start_dir: Path | None = None) -> Path | None:
    """Walk up from *start_dir* looking for a file that contains tasks.

    Returns the first match according to ``_SEARCH_FILES``, or ``None``.
    """
    if start_dir is None:
        start_dir = Path.cwd()
    return _walk_manifests(Path(start_dir), "has_tasks")


@lru_cache(maxsize=4)
def _cached_task_parse(path_str: str) -> dict[str, Task]:
    """Parse tasks from a manifest file (cached by path string)."""
    p = Path(path_str)
    parser = find_parser(p)
    return parser.parse_tasks(p)


def detect_and_parse_tasks(
    file_path: Path | None = None,
    start_dir: Path | None = None,
) -> tuple[Path, dict[str, Task]]:
    """Detect (or use *file_path*) a task file and parse it.

    Returns ``(resolved_path, {task_name: Task})``.
    Raises ``NoTaskFileError`` when no file is found.
    """
    if file_path is not None:
        path = file_path.resolve()
    else:
        found = detect_task_file(start_dir)
        if found is None:
            raise NoTaskFileError(str(start_dir or Path.cwd()))
        path = found
    tasks = _cached_task_parse(str(path))
    return path, tasks
