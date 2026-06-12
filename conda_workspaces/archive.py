"""Archive creation and extraction for conda workspaces.

Provides functions for collecting workspace files, creating tar archives
(gzip or zstandard), extracting with path traversal protection, bundling
conda packages for offline use, and inspecting archive contents.
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib
import shutil
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from conda_lockfiles.load_yaml import load_yaml

from .exceptions import (
    ArchiveError,
    ArchiveHashMismatchError,
    ArchivePathTraversalError,
)
from .paths import parse_relative_posix_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import PurePosixPath
    from typing import Any

    from .models import ArchiveConfig

ARCHIVE_SUFFIXES: tuple[str, ...] = (
    ".tar.zst",
    ".tar.zstd",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
)
"""Recognised archive filename suffixes, longest first."""

MANIFEST_FILENAMES = {"conda.toml", "pixi.toml", "pyproject.toml"}
"""Filenames recognised as workspace manifests inside an archive."""

CONDA_PACKAGE_SUFFIXES: tuple[str, ...] = (".conda", ".tar.bz2")
"""Recognised conda package archive suffixes."""

ALLOWED_TAR_TYPES: frozenset[bytes] = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
    }
)
"""Tar member types accepted during extraction."""

BUILTIN_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".conda/envs",
        ".pixi",
        "__pycache__",
    }
)
"""Directories excluded from archives regardless of user configuration."""


def parse_relative_archive_path(
    path: str,
    *,
    allow_parent: bool = False,
) -> PurePosixPath:
    """Return *path* as a validated POSIX archive path.

    Tar members and receipt paths use POSIX separators regardless of the
    host OS.  Keeping this policy in one helper lets extraction and receipt
    verification reject the same ambiguous path syntax while raising their
    own domain-specific errors.
    """
    try:
        return parse_relative_posix_path(
            path,
            allow_parent=allow_parent,
            require_canonical=True,
        )
    except ValueError as exc:
        raise ValueError(f"Invalid relative archive path: {path!r}") from exc


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


def tarfile_supports_zstd() -> bool:
    """Return True when this Python's tarfile module can open zstd archives."""
    return "zst" in tarfile.TarFile.OPEN_METH


def zstd_module() -> Any:
    """Return the stdlib or backport zstd module."""
    for module_name in ("compression.zstd", "backports.zstd"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    raise ArchiveError(
        "Zstandard archive support is not available.",
        hints=[
            "Install backports.zstd for Python versions before 3.14,",
            "or choose an archive name ending in .tar.gz or .tar.bz2.",
        ],
    )


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


@contextmanager
def open_tar_for_write(
    output: Path, compression: str, compression_level: int | None
) -> Iterator[tarfile.TarFile]:
    """Open a tar archive for writing, optionally setting compression level."""
    if compression == "zst" and not tarfile_supports_zstd():
        with zstd_module().open(output, "wb", level=compression_level) as compressed:
            with tarfile.open(fileobj=compressed, mode="w:") as tf:
                yield tf
        return

    mode = f"w:{compression}"
    kwargs = {}
    if compression_level is not None:
        kwargs["compresslevel"] = compression_level
    with tarfile.open(output, mode, **kwargs) as tf:  # ty: ignore[no-matching-overload]
        yield tf


def create_archive(
    root: Path,
    output: Path,
    archive_config: ArchiveConfig,
    *,
    bundle_packages: list[Path] | None = None,
) -> Path:
    """Create a tar archive of the workspace at *root*.

    Writes to *output*, creating parent directories as needed.
    If *bundle_packages* is provided, the listed conda package archives
    are added under a ``packages/`` prefix inside the archive.
    """
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    files = collect_archive_files(root, archive_config)
    files = [f for f in files if f.resolve() != output]

    compression = detect_compression(output)

    with open_tar_for_write(
        output, compression, archive_config.compression_level
    ) as tf:
        add_files_to_tar(tf, root, files)
        if bundle_packages:
            add_packages_to_tar(tf, bundle_packages)

    return output


def add_files_to_tar(tf: tarfile.TarFile, root: Path, files: list[Path]) -> None:
    """Add workspace *files* to the tar, using paths relative to *root*."""
    for path in files:
        arcname = path.relative_to(root).as_posix()
        tf.add(str(path), arcname=arcname)


def add_packages_to_tar(tf: tarfile.TarFile, packages: list[Path]) -> None:
    """Add conda package archives under the ``packages/`` archive prefix."""
    for pkg in packages:
        arcname = f"packages/{pkg.name}"
        tf.add(str(pkg), arcname=arcname)


def validate_tar_member(member: tarfile.TarInfo, target: Path) -> None:
    """Raise :class:`ArchivePathTraversalError` if *member* escapes *target*.

    Checks for disallowed file types (device nodes, FIFOs, etc.),
    absolute paths, ``..`` components, and symlink targets.
    """
    if member.type not in ALLOWED_TAR_TYPES:
        raise ArchivePathTraversalError(member.name)

    try:
        member_path = parse_relative_archive_path(member.name)
    except ValueError:
        raise ArchivePathTraversalError(member.name) from None

    try:
        resolved = target.joinpath(*member_path.parts).resolve()
        resolved.relative_to(target.resolve())
    except ValueError:
        raise ArchivePathTraversalError(member.name)

    if member.issym() or member.islnk():
        try:
            link_target = parse_relative_archive_path(
                member.linkname,
                allow_parent=True,
            )
        except ValueError:
            raise ArchivePathTraversalError(member.name) from None
        resolved_link = target.joinpath(
            *member_path.parent.parts,
            *link_target.parts,
        ).resolve()
        try:
            resolved_link.relative_to(target.resolve())
        except ValueError:
            raise ArchivePathTraversalError(member.name)


@contextmanager
def open_tar(archive_path: Path) -> Iterator[tarfile.TarFile]:
    """Open a tar archive, handling zstandard decompression transparently."""
    compression = detect_compression(archive_path)
    if compression == "zst" and not tarfile_supports_zstd():
        with zstd_module().open(archive_path, "rb") as compressed:
            with tarfile.open(fileobj=compressed, mode="r:") as tf:
                yield tf
        return
    with tarfile.open(  # ty: ignore[no-matching-overload]
        archive_path, f"r:{compression}"
    ) as tf:
        yield tf


def extract_archive(archive_path: Path, target: Path) -> Path:
    """Extract *archive_path* into *target* with path traversal protection.

    Every member is validated before extraction. On Python 3.12+ the
    ``filter="data"`` parameter provides additional defense-in-depth.
    """
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)

    with open_tar(archive_path) as tf:
        members = tf.getmembers()
        for member in members:
            validate_tar_member(member, target)
        if hasattr(tarfile, "data_filter"):
            tf.extractall(path=target, members=members, filter="data")
        else:
            tf.extractall(path=target, members=members)

    return target


def parse_lockfile_packages(lockfile_path: Path) -> list[dict]:
    """Parse the ``packages`` list from a conda lockfile."""
    data = load_yaml(lockfile_path)
    return data.get("packages", []) or []


def url_to_filename(url: str) -> str:
    """Extract the filename from a conda package URL."""
    filename = Path(urlsplit(url).path).name
    if not filename or not filename.endswith(CONDA_PACKAGE_SUFFIXES):
        raise ArchiveError(
            f"Cannot determine conda package filename from URL: {url}",
            hints=[
                "Expected package URLs to end in .conda or .tar.bz2.",
                "Regenerate conda.lock and retry the archive command.",
            ],
        )
    return filename


def collect_bundle_packages(
    lockfile_path: Path,
    cache_dirs: list[Path],
) -> list[Path]:
    """Locate conda packages referenced by the lockfile in local caches.

    Raises :class:`ArchiveError` if any package is missing from all caches.
    """
    packages_data = parse_lockfile_packages(lockfile_path)
    result: list[Path] = []
    seen: dict[str, str | None] = {}

    for pkg in packages_data:
        url = pkg.get("conda") or pkg.get("url", "")
        if not url:
            continue
        filename = url_to_filename(url)
        sha256 = pkg.get("sha256")
        fingerprint = str(sha256) if sha256 is not None else None
        if filename in seen:
            previous = seen[filename]
            if previous is None or fingerprint is None or previous != fingerprint:
                raise ArchiveError(
                    f"Package filename collision in lockfile: {filename}",
                    hints=[
                        "The archive bundle stores package archives by filename.",
                        "Regenerate the lockfile or remove one of the colliding"
                        " packages before bundling.",
                    ],
                )
            continue
        seen[filename] = fingerprint

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


def file_sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of *path* without reading it all at once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            raise ArchiveError(
                f"Cannot verify bundled package '{pkg_path.name}'.",
                hints=[
                    "No SHA256 entry for this package was found in conda.lock.",
                    "Regenerate conda.lock with a current conda-workspaces version"
                    " before bundling or priming package caches.",
                ],
            )
        actual_hash = file_sha256(pkg_path)
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
    if not lockfile.is_file():
        raise ArchiveError(
            "Cannot prime package cache: bundled packages require conda.lock.",
            hints=[
                "Extract the archive without cache priming using --no-install,",
                "or rebuild the archive with its lockfile included.",
            ],
        )

    packages = sorted(
        path
        for suffix in CONDA_PACKAGE_SUFFIXES
        for path in packages_dir.glob(f"*{suffix}")
    )
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
        n
        for n in names
        if n.startswith("packages/") and n.endswith(CONDA_PACKAGE_SUFFIXES)
    ]

    return {
        "has_manifest": bool(names & MANIFEST_FILENAMES),
        "has_lockfile": "conda.lock" in names,
        "has_packages": len(package_members) > 0,
        "package_count": len(package_members),
    }
