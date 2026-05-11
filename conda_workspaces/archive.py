"""Archive creation and extraction for conda workspaces."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

from .exceptions import ArchiveError, ArchiveHashMismatchError, ArchivePathTraversalError

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


# ---------------------------------------------------------------------------
# Task 4: tarball creation
# ---------------------------------------------------------------------------


def _detect_compression(output: Path) -> str:
    name = output.name
    if name.endswith(".tar.zst") or name.endswith(".tar.zstd"):
        return "zst"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "gz"
    if name.endswith(".tar.bz2"):
        return "bz2"
    return "gz"


def create_archive(
    root: Path,
    output: Path,
    archive_config: ArchiveConfig,
    *,
    bundle_packages: list[Path] | None = None,
) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    files = collect_archive_files(root, archive_config)
    files = [f for f in files if f.resolve() != output]

    compression = _detect_compression(output)

    if compression == "zst":
        _write_tar_zst(root, output, files, bundle_packages)
    else:
        mode = f"w:{compression}"
        with tarfile.open(output, mode) as tf:
            _add_files_to_tar(tf, root, files)
            if bundle_packages:
                _add_packages_to_tar(tf, bundle_packages)

    return output


def _add_files_to_tar(tf: tarfile.TarFile, root: Path, files: list[Path]) -> None:
    for path in files:
        arcname = str(path.relative_to(root))
        tf.add(str(path), arcname=arcname)


def _add_packages_to_tar(tf: tarfile.TarFile, packages: list[Path]) -> None:
    for pkg in packages:
        arcname = f"packages/{pkg.name}"
        tf.add(str(pkg), arcname=arcname)


def _write_tar_zst(
    root: Path,
    output: Path,
    files: list[Path],
    bundle_packages: list[Path] | None,
) -> None:
    import zstandard

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        _add_files_to_tar(tf, root, files)
        if bundle_packages:
            _add_packages_to_tar(tf, bundle_packages)

    cctx = zstandard.ZstdCompressor(level=3)
    compressed = cctx.compress(buf.getvalue())
    output.write_bytes(compressed)


# ---------------------------------------------------------------------------
# Task 5: safe extraction with path traversal protection
# ---------------------------------------------------------------------------


def _validate_tar_member(member: tarfile.TarInfo, target: Path) -> None:
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


def _open_tar(archive_path: Path) -> tarfile.TarFile:
    compression = _detect_compression(archive_path)
    if compression == "zst":
        import zstandard

        with open(archive_path, "rb") as fh:
            dctx = zstandard.ZstdDecompressor()
            decompressed = dctx.decompress(fh.read())
        return tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:")
    else:
        return tarfile.open(archive_path, f"r:{compression}")


def extract_archive(archive_path: Path, target: Path) -> Path:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)

    with _open_tar(archive_path) as tf:
        for member in tf.getmembers():
            _validate_tar_member(member, target)
        tf.extractall(path=target, filter="data")

    return target


# ---------------------------------------------------------------------------
# Task 6: package bundling and hash verification
# ---------------------------------------------------------------------------


def _parse_lockfile_packages(lockfile_path: Path) -> list[dict]:
    from conda_lockfiles.load_yaml import load_yaml

    data = load_yaml(lockfile_path)
    return data.get("packages", []) or []


def _url_to_filename(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def collect_bundle_packages(
    lockfile_path: Path,
    cache_dirs: list[Path],
) -> list[Path]:
    packages_data = _parse_lockfile_packages(lockfile_path)
    result: list[Path] = []
    seen: set[str] = set()

    for pkg in packages_data:
        url = pkg.get("conda") or pkg.get("url", "")
        if not url:
            continue
        filename = _url_to_filename(url)
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


def _build_hash_index(lockfile_path: Path) -> dict[str, str]:
    packages_data = _parse_lockfile_packages(lockfile_path)
    index: dict[str, str] = {}
    for pkg in packages_data:
        url = pkg.get("conda") or pkg.get("url", "")
        sha256 = pkg.get("sha256")
        if url and sha256 is not None:
            index[_url_to_filename(url)] = str(sha256)
    return index


def verify_package_hashes(
    packages: list[Path],
    lockfile_path: Path,
) -> None:
    expected = _build_hash_index(lockfile_path)

    for pkg_path in packages:
        exp_hash = expected.get(pkg_path.name)
        if not exp_hash:
            continue
        actual_hash = hashlib.sha256(pkg_path.read_bytes()).hexdigest()
        if actual_hash != exp_hash:
            raise ArchiveHashMismatchError(
                pkg_path.name, expected=exp_hash, actual=actual_hash
            )


# ---------------------------------------------------------------------------
# Task 7: unarchive with cache priming
# ---------------------------------------------------------------------------


def prime_package_cache(
    extracted_dir: Path,
    cache_dir: Path,
) -> int:
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
