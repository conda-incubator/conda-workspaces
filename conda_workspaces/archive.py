"""Archive creation and extraction for conda workspaces.

Provides functions for collecting workspace files, creating tar archives
(gzip or zstandard), extracting with path traversal protection, bundling
conda packages for offline use, and inspecting archive contents.
"""

from __future__ import annotations

import fnmatch
import hashlib
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

from conda_lockfiles.load_yaml import load_yaml

from .exceptions import (
    ArchiveError,
    ArchiveHashMismatchError,
    ArchivePathTraversalError,
)

try:
    import backports.zstd  # noqa: F401 -- registers zstd codec with tarfile
except ImportError:
    pass  # Python 3.14+ has native support

if TYPE_CHECKING:
    from .models import ArchiveConfig

BUILTIN_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".conda/envs",
        ".pixi",
        "__pycache__",
    }
)
"""Directories excluded from archives regardless of user configuration."""


def is_git_repo(root: Path) -> bool:
    """Return True if *root* is inside a git working tree."""
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


def git_tracked_files(root: Path) -> list[Path]:
    """Return absolute paths for all git-tracked files under *root*."""
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


def is_excluded_by_builtins(rel_path: str) -> bool:
    """Return True if *rel_path* falls under a builtin-excluded directory."""
    for excl in BUILTIN_EXCLUDE_DIRS:
        if rel_path == excl or rel_path.startswith(excl + "/"):
            return True
    return False


def is_excluded_by_patterns(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """Return True if *rel_path* matches any of the user-supplied glob *patterns*."""
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
    """Collect workspace files eligible for archiving.

    In git repos, only tracked files are included. Otherwise all files
    under *root* are considered, filtered by builtin and user excludes.
    """
    if is_git_repo(root):
        candidates = git_tracked_files(root)
    else:
        candidates = [p for p in root.rglob("*") if p.is_file()]

    result: list[Path] = []
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        if is_excluded_by_builtins(rel):
            continue
        if archive_config.include and not is_included_by_patterns(
            rel, archive_config.include
        ):
            continue
        if is_excluded_by_patterns(rel, archive_config.exclude):
            continue
        result.append(path)

    return sorted(result)


def detect_compression(output: Path) -> str:
    """Infer compression format from the archive filename extension."""
    name = output.name
    if name.endswith(".tar.zst") or name.endswith(".tar.zstd"):
        return "zst"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "gz"
    if name.endswith(".tar.bz2"):
        return "bz2"
    return "zst"


def is_included_by_patterns(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """Return True if *rel_path* matches any include pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        parts = rel_path.split("/")
        for i in range(len(parts)):
            partial = "/".join(parts[: i + 1])
            if fnmatch.fnmatch(partial, pattern):
                return True
    return False


def _open_tar_for_write(
    output: Path, mode: str, compression_level: int | None
) -> tarfile.TarFile:
    """Open a tar archive for writing, optionally setting compression level."""
    if compression_level is not None:
        return tarfile.open(  # type: ignore[call-overload]
            output, mode, compresslevel=compression_level
        )
    return tarfile.open(output, mode)  # type: ignore[call-overload]


def create_archive(
    root: Path,
    output: Path,
    archive_config: ArchiveConfig,
    *,
    bundle_packages: list[Path] | None = None,
) -> Path:
    """Create a tar archive of the workspace at *root*.

    Writes to *output*, creating parent directories as needed.
    If *bundle_packages* is provided, the listed ``.conda`` files
    are added under a ``packages/`` prefix inside the archive.
    """
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    files = collect_archive_files(root, archive_config)
    files = [f for f in files if f.resolve() != output]

    compression = detect_compression(output)
    mode = f"w:{compression}"

    with _open_tar_for_write(output, mode, archive_config.compression_level) as tf:
        add_files_to_tar(tf, root, files)
        if bundle_packages:
            add_packages_to_tar(tf, bundle_packages)

    return output


def add_files_to_tar(tf: tarfile.TarFile, root: Path, files: list[Path]) -> None:
    """Add workspace *files* to the tar, using paths relative to *root*."""
    for path in files:
        arcname = str(path.relative_to(root))
        tf.add(str(path), arcname=arcname)


def add_packages_to_tar(tf: tarfile.TarFile, packages: list[Path]) -> None:
    """Add ``.conda`` *packages* under the ``packages/`` archive prefix."""
    for pkg in packages:
        arcname = f"packages/{pkg.name}"
        tf.add(str(pkg), arcname=arcname)


def validate_tar_member(member: tarfile.TarInfo, target: Path) -> None:
    """Raise :class:`ArchivePathTraversalError` if *member* escapes *target*.

    Checks absolute paths, ``..`` components, and symlink targets.
    """
    member_path = Path(member.name)

    if member_path.is_absolute():
        raise ArchivePathTraversalError(member.name)

    try:
        resolved = (target / member_path).resolve()
        resolved.relative_to(target.resolve())
    except ValueError:
        raise ArchivePathTraversalError(member.name)

    if ".." in member_path.parts:
        raise ArchivePathTraversalError(member.name)

    if member.issym() or member.islnk():
        link_target = Path(member.linkname)
        if link_target.is_absolute():
            raise ArchivePathTraversalError(member.name)
        resolved_link = (target / member_path.parent / link_target).resolve()
        try:
            resolved_link.relative_to(target.resolve())
        except ValueError:
            raise ArchivePathTraversalError(member.name)


def open_tar(archive_path: Path) -> tarfile.TarFile:
    """Open a tar archive, handling zstandard decompression transparently."""
    compression = detect_compression(archive_path)
    return tarfile.open(archive_path, f"r:{compression}")


def extract_archive(archive_path: Path, target: Path) -> Path:
    """Extract *archive_path* into *target* with path traversal protection.

    Every member is validated before extraction. On Python 3.12+ the
    ``filter="data"`` parameter provides additional defense-in-depth.
    """
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)

    with open_tar(archive_path) as tf:
        for member in tf.getmembers():
            validate_tar_member(member, target)
        if sys.version_info >= (3, 12):
            tf.extractall(path=target, filter="data")
        else:
            tf.extractall(path=target)

    return target


def parse_lockfile_packages(lockfile_path: Path) -> list[dict]:
    """Parse the ``packages`` list from a conda lockfile."""
    data = load_yaml(lockfile_path)
    return data.get("packages", []) or []


def url_to_filename(url: str) -> str:
    """Extract the filename from a conda package URL."""
    return url.rsplit("/", 1)[-1]


def collect_bundle_packages(
    lockfile_path: Path,
    cache_dirs: list[Path],
) -> list[Path]:
    """Locate ``.conda`` packages referenced by the lockfile in local caches.

    Raises :class:`ArchiveError` if any package is missing from all caches.
    """
    packages_data = parse_lockfile_packages(lockfile_path)
    result: list[Path] = []
    seen: set[str] = set()

    for pkg in packages_data:
        url = pkg.get("conda") or pkg.get("url", "")
        if not url:
            continue
        filename = url_to_filename(url)
        if filename in seen:
            continue
        seen.add(filename)

        found = False
        for cache_dir in cache_dirs:
            candidate = cache_dir / filename
            if candidate.is_file():
                result.append(candidate)
                found = True
                break

        if not found:
            raise ArchiveError(
                f"Package '{filename}' not found in cache.",
                hints=[
                    "Run 'conda workspace install' to populate the package cache,",
                    "then retry the archive command.",
                ],
            )

    return sorted(result, key=lambda p: p.name)


def build_hash_index(lockfile_path: Path) -> dict[str, str]:
    """Build a filename-to-SHA256 mapping from lockfile package entries."""
    packages_data = parse_lockfile_packages(lockfile_path)
    index: dict[str, str] = {}
    for pkg in packages_data:
        url = pkg.get("conda") or pkg.get("url", "")
        sha256 = pkg.get("sha256")
        if url and sha256 is not None:
            index[url_to_filename(url)] = str(sha256)
    return index


def verify_package_hashes(
    packages: list[Path],
    lockfile_path: Path,
) -> None:
    """Verify SHA256 hashes of *packages* against the lockfile.

    Raises :class:`ArchiveHashMismatchError` on the first mismatch.
    """
    expected = build_hash_index(lockfile_path)

    for pkg_path in packages:
        exp_hash = expected.get(pkg_path.name)
        if not exp_hash:
            continue
        actual_hash = hashlib.sha256(pkg_path.read_bytes()).hexdigest()
        if actual_hash != exp_hash:
            raise ArchiveHashMismatchError(
                pkg_path.name, expected=exp_hash, actual=actual_hash
            )


def prime_package_cache(
    extracted_dir: Path,
    cache_dir: Path,
) -> int:
    """Copy bundled packages from an extracted archive into the conda cache.

    Verifies SHA256 hashes against the lockfile before copying.
    Returns the number of packages added to the cache.
    """
    packages_dir = extracted_dir / "packages"
    if not packages_dir.is_dir():
        return 0

    lockfile = extracted_dir / "conda.lock"
    packages = sorted(packages_dir.glob("*.conda"))
    if not packages:
        return 0

    verify_package_hashes(packages, lockfile)

    cache_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for pkg in packages:
        dest = cache_dir / pkg.name
        if not dest.exists():
            shutil.copy2(pkg, dest)
            count += 1

    return count


def inspect_archive(archive_path: Path) -> dict[str, object]:
    """Return metadata about an archive without extracting it."""
    with open_tar(archive_path) as tf:
        names = set(tf.getnames())

    package_members = [
        n for n in names if n.startswith("packages/") and n.endswith(".conda")
    ]

    return {
        "has_manifest": "conda.toml" in names,
        "has_lockfile": "conda.lock" in names,
        "has_attestation": "conda.lock.sigstore" in names,
        "has_packages": len(package_members) > 0,
        "package_count": len(package_members),
    }
