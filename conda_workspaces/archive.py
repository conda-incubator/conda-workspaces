"""Archive creation and extraction for conda workspaces."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ArchiveConfig

BUILTIN_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".conda/envs",
    ".pixi",
    "__pycache__",
})


def _is_git_repo(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except FileNotFoundError:
        return False


def _git_tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = []
    for entry in result.stdout.split("\0"):
        if entry:
            full = root / entry
            if full.is_file():
                paths.append(full)
    return paths


def _is_excluded_by_builtins(rel_path: str) -> bool:
    for excl in BUILTIN_EXCLUDE_DIRS:
        if rel_path == excl or rel_path.startswith(excl + "/"):
            return True
    return False


def _is_excluded_by_patterns(rel_path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        parts = rel_path.split("/")
        for i in range(len(parts)):
            partial = "/".join(parts[: i + 1])
            if fnmatch.fnmatch(partial, pattern):
                return True
    return False


def collect_archive_files(
    root: Path,
    archive_config: ArchiveConfig,
) -> list[Path]:
    if _is_git_repo(root):
        candidates = _git_tracked_files(root)
    else:
        candidates = [p for p in root.rglob("*") if p.is_file()]

    result: list[Path] = []
    for path in candidates:
        rel = str(path.relative_to(root))
        if _is_excluded_by_builtins(rel):
            continue
        if _is_excluded_by_patterns(rel, archive_config.exclude):
            continue
        result.append(path)

    return sorted(result)
