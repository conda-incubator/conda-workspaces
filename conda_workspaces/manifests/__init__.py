"""Manifest detection and parser registry (workspaces and tasks).

Search order (same for workspaces and tasks)
--------------------------------------------
1. ``conda.toml``     -- conda-native manifest format
2. ``pixi.toml``      -- pixi-native format (compatibility)
3. ``pyproject.toml``  -- pixi or conda tables embedded

The first file that exists *and* contains the relevant configuration wins.
"""

from __future__ import annotations

import os
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


def walk_manifests(
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
                for parser in _PARSERS:
                    if parser.can_handle(candidate) and getattr(parser, predicate)(
                        candidate
                    ):
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

    result = walk_manifests(start_dir, "has_workspace")
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
def cached_parse(path_str: str) -> WorkspaceConfig:
    """Parse a workspace config from *path_str* (cached by path string)."""
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
    config = cached_parse(str(path))
    return path, config


def detect_task_file(start_dir: Path | None = None) -> Path | None:
    """Walk up from *start_dir* looking for a file that contains tasks.

    Returns the first match according to ``_SEARCH_FILES``, or ``None``.
    """
    if start_dir is None:
        start_dir = Path.cwd()
    return walk_manifests(Path(start_dir), "has_tasks")


@lru_cache(maxsize=4)
def cached_task_parse(path_str: str) -> dict[str, Task]:
    """Parse tasks from a manifest file (cached by path string)."""
    p = Path(path_str)
    parser = find_parser(p)
    return parser.parse_tasks(p)


@lru_cache(maxsize=1)
def cached_user_task_parse(path_str: str) -> dict[str, Task]:
    """Parse tasks from the user-level ``tasks.toml`` (conda.toml format)."""
    return CondaTomlParser().parse_tasks(Path(path_str))


def user_task_file() -> Path | None:
    """Return the user-level task file path, or ``None``."""
    candidates: list[Path] = []
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        candidates.append(Path(xdg) / "conda" / "tasks.toml")
    candidates.append(Path.home() / ".config" / "conda" / "tasks.toml")
    candidates.append(Path.home() / ".conda" / "tasks.toml")
    for c in candidates:
        if c.is_file():
            return c
    return None


def detect_and_parse_tasks_with_origin(
    file_path: Path | None = None,
    start_dir: Path | None = None,
) -> tuple[Path, dict[str, Task], set[str]]:
    """Detect task files and parse them, tracking user-level task origin.

    Returns ``(resolved_path, {task_name: Task}, user_only_names)`` where
    *user_only_names* is the set of task names that came from the user-level
    file and were not overridden by the project.

    Raises ``NoTaskFileError`` when neither a project nor a user task file
    is found.
    """
    project_path: Path | None = None
    if file_path is not None:
        project_path = file_path.resolve()
    else:
        project_path = detect_task_file(start_dir)

    user_path = user_task_file()

    if project_path is None and user_path is None:
        raise NoTaskFileError(str(start_dir or Path.cwd()))

    user_tasks: dict[str, Task] = {}
    if user_path is not None:
        user_tasks = cached_user_task_parse(str(user_path))

    if project_path is not None:
        project_tasks = cached_task_parse(str(project_path))
        merged = {**user_tasks, **project_tasks}
        user_only = set(user_tasks) - set(project_tasks)
        return project_path, merged, user_only

    assert user_path is not None
    return user_path, dict(user_tasks), set(user_tasks)


def detect_and_parse_tasks(
    file_path: Path | None = None,
    start_dir: Path | None = None,
) -> tuple[Path, dict[str, Task]]:
    """Detect (or use *file_path*) a task file and parse it.

    Returns ``(resolved_path, {task_name: Task})``.
    Raises ``NoTaskFileError`` when no file is found.
    """
    path, tasks, _ = detect_and_parse_tasks_with_origin(file_path, start_dir)
    return path, tasks
