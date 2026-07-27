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


def output_paths_collide(left: Path, right: Path) -> bool:
    """Return whether two output paths address the same file target."""
    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return left.samefile(right)
    except (FileNotFoundError, OSError):
        return False


def validate_path_parent(path: Path) -> None:
    """Require the nearest existing parent of *path* to be a directory.

    Write previews use this read-only check before their mutation boundary so
    a file or broken symlink in the parent chain fails exactly as execution
    would.
    """
    parent = path.parent
    while not parent.exists() and not parent.is_symlink() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        raise NotADirectoryError(f"Path parent is not a directory: {parent}")


def validate_file_output(path: Path) -> None:
    """Validate the existing shape of a prospective output file path."""
    validate_path_parent(path)
    if path.is_symlink() and not path.exists():
        target = path.resolve(strict=False)
        if not target.parent.is_dir():
            raise FileNotFoundError(f"Symlink target parent does not exist: {target}")
        return
    if path.exists() and not path.is_file():
        raise IsADirectoryError(f"Output path is not a file: {path}")


def validate_directory_output(path: Path) -> None:
    """Validate the existing shape of a prospective output directory path."""
    validate_path_parent(path)
    if path.exists() and not path.is_dir():
        raise FileExistsError(f"Output path is not a directory: {path}")
    if path.is_symlink() and not path.exists():
        raise FileExistsError(f"Output path is a broken symlink: {path}")
