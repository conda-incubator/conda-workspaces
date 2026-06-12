"""Path validation helpers shared across workspace boundaries."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def has_absolute_path_syntax(path: str) -> bool:
    """Return whether *path* is absolute using POSIX or Windows syntax."""
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


def parse_relative_posix_path(
    path: str,
    *,
    allow_parent: bool = False,
    require_canonical: bool = False,
) -> PurePosixPath:
    """Return *path* as a validated POSIX relative path.

    This lives at module level because archives, receipts, and imported
    manifests all need the same host-independent path syntax policy while
    raising domain-specific errors at their own call sites.
    """
    posix_path = PurePosixPath(path)
    windows_path = PureWindowsPath(path)
    windows_parts = tuple(windows_path.parts)
    posix_parts = tuple(posix_path.parts)
    if (
        not path
        or "\0" in path
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
        or not posix_parts
        or windows_parts != posix_parts
        or (require_canonical and posix_path.as_posix() != path)
        or (not allow_parent and any(part == ".." for part in posix_parts))
    ):
        raise ValueError(f"Invalid relative path: {path!r}")
    return posix_path


def is_path_segment(value: str) -> bool:
    """Return whether *value* is one portable path segment.

    This is for manifest values that are used as filenames or directory
    names, not paths.  It checks POSIX and Windows parsing so a value that
    is harmless on the host OS but path-like elsewhere is still rejected.
    """
    try:
        return len(parse_relative_posix_path(value, require_canonical=True).parts) == 1
    except ValueError:
        return False


def resolve_relative_path(root: Path, path: PurePosixPath) -> Path:
    """Resolve *path* under *root*, rejecting symlink escapes."""
    root = root.resolve(strict=False)
    resolved = root.joinpath(*path.parts).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes root: {path!s}") from exc
    return resolved
