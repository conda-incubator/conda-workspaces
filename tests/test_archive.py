from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from conda_workspaces.archive import collect_archive_files
from conda_workspaces.models import ArchiveConfig


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory with various files."""
    (tmp_path / "conda.toml").write_text("[workspace]\nname = 'test'\n")
    (tmp_path / "conda.lock").write_text("version: 1\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "big.bin").write_text("binary data\n")
    (tmp_path / ".env").write_text("SECRET=abc\n")
    return tmp_path


@pytest.fixture
def git_project(project_dir: Path) -> Path:
    """Initialize a git repo and track some files."""
    subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "add", "conda.toml", "conda.lock", "src/main.py"],
        cwd=project_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project_dir, check=True, capture_output=True,
    )
    return project_dir


def test_collect_files_git_tracked(git_project: Path) -> None:
    config = ArchiveConfig()
    files = collect_archive_files(git_project, config)
    rel_paths = {str(f.relative_to(git_project)) for f in files}
    assert "conda.toml" in rel_paths
    assert "conda.lock" in rel_paths
    assert "src/main.py" in rel_paths
    assert ".env" not in rel_paths
    assert "data/big.bin" not in rel_paths


def test_collect_files_non_git(project_dir: Path) -> None:
    config = ArchiveConfig()
    files = collect_archive_files(project_dir, config)
    rel_paths = {str(f.relative_to(project_dir)) for f in files}
    assert "conda.toml" in rel_paths
    assert "src/main.py" in rel_paths
    assert "data/big.bin" in rel_paths
    assert ".env" in rel_paths


def test_collect_files_builtin_exclusions(project_dir: Path) -> None:
    (project_dir / ".git").mkdir()
    (project_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (project_dir / ".conda").mkdir()
    (project_dir / ".conda" / "envs").mkdir()
    (project_dir / ".conda" / "envs" / "default").mkdir()
    (project_dir / ".conda" / "envs" / "default" / "marker").write_text("")
    (project_dir / ".pixi").mkdir()
    (project_dir / ".pixi" / "envs").mkdir()

    config = ArchiveConfig()
    files = collect_archive_files(project_dir, config)
    rel_strs = {str(f.relative_to(project_dir)) for f in files}

    assert not any(p.startswith(".git/") or p == ".git" for p in rel_strs)
    assert not any(p.startswith(".conda/envs") for p in rel_strs)
    assert not any(p.startswith(".pixi/") for p in rel_strs)


def test_collect_files_custom_exclude(project_dir: Path) -> None:
    config = ArchiveConfig(exclude=("data/**",))
    files = collect_archive_files(project_dir, config)
    rel_paths = {str(f.relative_to(project_dir)) for f in files}
    assert "data/big.bin" not in rel_paths
    assert "conda.toml" in rel_paths


# ---------------------------------------------------------------------------
# Task 4: tarball creation
# ---------------------------------------------------------------------------

from conda_workspaces.archive import create_archive


def test_create_archive_tar_gz(project_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "project.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, output, config)

    assert output.is_file()
    with tarfile.open(output, "r:gz") as tf:
        names = tf.getnames()
    assert "conda.toml" in names
    assert "conda.lock" in names
    assert "src/main.py" in names


def test_create_archive_tar_zst(project_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "project.tar.zst"
    config = ArchiveConfig()
    create_archive(project_dir, output, config)

    assert output.is_file()
    import zstandard

    dctx = zstandard.ZstdDecompressor()
    with open(output, "rb") as fh:
        decompressed = dctx.decompress(fh.read())
    with tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:") as tf:
        names = tf.getnames()
    assert "conda.toml" in names


def test_create_archive_excludes_self(project_dir: Path) -> None:
    output = project_dir / "project.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, output, config)

    with tarfile.open(output, "r:gz") as tf:
        names = tf.getnames()
    assert "project.tar.gz" not in names


def test_create_archive_output_dir_created(project_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "deep" / "nested" / "archive.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, output, config)
    assert output.is_file()


# ---------------------------------------------------------------------------
# Task 5: safe extraction with path traversal protection
# ---------------------------------------------------------------------------

from conda_workspaces.archive import extract_archive
from conda_workspaces.exceptions import ArchivePathTraversalError


def test_extract_archive_basic(project_dir: Path, tmp_path: Path) -> None:
    archive_path = tmp_path / "test.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, archive_path, config)

    target = tmp_path / "extracted"
    result = extract_archive(archive_path, target)

    assert result == target
    assert (target / "conda.toml").is_file()
    assert (target / "conda.lock").is_file()
    assert (target / "src" / "main.py").is_file()


def test_extract_archive_path_traversal_blocked(tmp_path: Path) -> None:
    evil_archive = tmp_path / "evil.tar.gz"
    with tarfile.open(evil_archive, "w:gz") as tf:
        info = tarfile.TarInfo(name="../../../etc/passwd")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"evil"))

    target = tmp_path / "safe"
    with pytest.raises(ArchivePathTraversalError, match="etc/passwd"):
        extract_archive(evil_archive, target)

    assert not (target / "etc" / "passwd").exists()


def test_extract_archive_absolute_path_blocked(tmp_path: Path) -> None:
    evil_archive = tmp_path / "abs.tar.gz"
    with tarfile.open(evil_archive, "w:gz") as tf:
        info = tarfile.TarInfo(name="/tmp/evil_file")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"evil"))

    target = tmp_path / "safe"
    with pytest.raises(ArchivePathTraversalError):
        extract_archive(evil_archive, target)


def test_extract_archive_symlink_escape_blocked(tmp_path: Path) -> None:
    evil_archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(evil_archive, "w:gz") as tf:
        info = tarfile.TarInfo(name="escape")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../../etc"
        tf.addfile(info)

    target = tmp_path / "safe"
    with pytest.raises(ArchivePathTraversalError):
        extract_archive(evil_archive, target)


def test_extract_archive_zst(project_dir: Path, tmp_path: Path) -> None:
    archive_path = tmp_path / "test.tar.zst"
    config = ArchiveConfig()
    create_archive(project_dir, archive_path, config)

    target = tmp_path / "extracted"
    extract_archive(archive_path, target)

    assert (target / "conda.toml").is_file()
    assert (target / "src" / "main.py").is_file()


# ---------------------------------------------------------------------------
# Task 6: package bundling and hash verification
# ---------------------------------------------------------------------------

import hashlib

from conda_workspaces.archive import (
    collect_bundle_packages,
    verify_package_hashes,
)
from conda_workspaces.exceptions import ArchiveHashMismatchError


@pytest.fixture
def lockfile_with_packages(project_dir: Path) -> Path:
    """Create a conda.lock with fake package entries and matching .conda files."""
    pkg_content = b"fake conda package data"
    sha256 = hashlib.sha256(pkg_content).hexdigest()

    lockfile_content = f"""\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/linux-64/zlib-1.2.13-h4dc568a_6.conda
      osx-arm64:
        - conda: https://conda.anaconda.org/conda-forge/osx-arm64/zlib-1.2.13-h53f4e23_6.conda
packages:
  - conda: https://conda.anaconda.org/conda-forge/linux-64/zlib-1.2.13-h4dc568a_6.conda
    sha256: {sha256}
    md5: abc123
    name: zlib
    version: 1.2.13
    build: h4dc568a_6
    subdir: linux-64
    depends: []
  - conda: https://conda.anaconda.org/conda-forge/osx-arm64/zlib-1.2.13-h53f4e23_6.conda
    sha256: {sha256}
    md5: def456
    name: zlib
    version: 1.2.13
    build: h53f4e23_6
    subdir: osx-arm64
    depends: []
"""
    (project_dir / "conda.lock").write_text(lockfile_content, encoding="utf-8")

    cache_dir = project_dir / "pkg_cache"
    cache_dir.mkdir()
    (cache_dir / "zlib-1.2.13-h4dc568a_6.conda").write_bytes(pkg_content)
    (cache_dir / "zlib-1.2.13-h53f4e23_6.conda").write_bytes(pkg_content)

    return project_dir


def test_collect_bundle_packages(lockfile_with_packages: Path) -> None:
    cache_dir = lockfile_with_packages / "pkg_cache"
    lockfile = lockfile_with_packages / "conda.lock"
    packages = collect_bundle_packages(lockfile, [cache_dir])
    assert len(packages) == 2
    filenames = {p.name for p in packages}
    assert "zlib-1.2.13-h4dc568a_6.conda" in filenames
    assert "zlib-1.2.13-h53f4e23_6.conda" in filenames


def test_verify_package_hashes_pass(lockfile_with_packages: Path) -> None:
    cache_dir = lockfile_with_packages / "pkg_cache"
    lockfile = lockfile_with_packages / "conda.lock"
    packages = collect_bundle_packages(lockfile, [cache_dir])
    verify_package_hashes(packages, lockfile)


def test_verify_package_hashes_fail(lockfile_with_packages: Path) -> None:
    cache_dir = lockfile_with_packages / "pkg_cache"
    lockfile = lockfile_with_packages / "conda.lock"
    (cache_dir / "zlib-1.2.13-h4dc568a_6.conda").write_bytes(b"tampered")
    packages = collect_bundle_packages(lockfile, [cache_dir])
    with pytest.raises(ArchiveHashMismatchError, match="zlib-1.2.13-h4dc568a_6"):
        verify_package_hashes(packages, lockfile)


def test_create_archive_with_bundle(lockfile_with_packages: Path, tmp_path: Path) -> None:
    cache_dir = lockfile_with_packages / "pkg_cache"
    lockfile = lockfile_with_packages / "conda.lock"
    packages = collect_bundle_packages(lockfile, [cache_dir])

    output = tmp_path / "bundled.tar.gz"
    config = ArchiveConfig()
    create_archive(lockfile_with_packages, output, config, bundle_packages=packages)

    with tarfile.open(output, "r:gz") as tf:
        names = tf.getnames()
    assert "packages/zlib-1.2.13-h4dc568a_6.conda" in names
    assert "packages/zlib-1.2.13-h53f4e23_6.conda" in names
    assert "conda.toml" in names


# ---------------------------------------------------------------------------
# Task 7: cache priming
# ---------------------------------------------------------------------------

from conda_workspaces.archive import prime_package_cache


def test_prime_package_cache(tmp_path: Path) -> None:
    pkg_content = b"fake package content"
    sha256 = hashlib.sha256(pkg_content).hexdigest()

    extracted = tmp_path / "project"
    extracted.mkdir()
    (extracted / "packages").mkdir()
    (extracted / "packages" / "numpy-1.26-h1234.conda").write_bytes(pkg_content)

    lockfile_content = f"""\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/linux-64/numpy-1.26-h1234.conda
packages:
  - conda: https://conda.anaconda.org/conda-forge/linux-64/numpy-1.26-h1234.conda
    sha256: {sha256}
    name: numpy
    version: "1.26"
    build: h1234
    subdir: linux-64
    depends: []
"""
    (extracted / "conda.lock").write_text(lockfile_content, encoding="utf-8")

    cache_dir = tmp_path / "pkgs"
    cache_dir.mkdir()

    count = prime_package_cache(extracted, cache_dir)

    assert count == 1
    assert (cache_dir / "numpy-1.26-h1234.conda").is_file()
    assert (cache_dir / "numpy-1.26-h1234.conda").read_bytes() == pkg_content


def test_prime_package_cache_no_packages(tmp_path: Path) -> None:
    extracted = tmp_path / "project"
    extracted.mkdir()
    (extracted / "conda.lock").write_text("version: 1\nenvironments: {}\npackages: []\n")

    cache_dir = tmp_path / "pkgs"
    cache_dir.mkdir()

    count = prime_package_cache(extracted, cache_dir)
    assert count == 0


def test_prime_package_cache_hash_mismatch(tmp_path: Path) -> None:
    extracted = tmp_path / "project"
    extracted.mkdir()
    (extracted / "packages").mkdir()
    (extracted / "packages" / "bad-1.0-h000.conda").write_bytes(b"tampered")

    lockfile_content = """\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/linux-64/bad-1.0-h000.conda
packages:
  - conda: https://conda.anaconda.org/conda-forge/linux-64/bad-1.0-h000.conda
    sha256: 0000000000000000000000000000000000000000000000000000000000000000
    name: bad
    version: "1.0"
    build: h000
    subdir: linux-64
    depends: []
"""
    (extracted / "conda.lock").write_text(lockfile_content, encoding="utf-8")

    cache_dir = tmp_path / "pkgs"
    cache_dir.mkdir()

    with pytest.raises(ArchiveHashMismatchError, match="bad-1.0-h000"):
        prime_package_cache(extracted, cache_dir)


# ---------------------------------------------------------------------------
# Task 8: archive inspection helper
# ---------------------------------------------------------------------------

from conda_workspaces.archive import inspect_archive


def test_inspect_archive_lightweight(project_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "test.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, output, config)

    info = inspect_archive(output)
    assert info["has_manifest"] is True
    assert info["has_lockfile"] is True
    assert info["has_packages"] is False
    assert info["has_attestation"] is False


def test_inspect_archive_bundled(lockfile_with_packages: Path, tmp_path: Path) -> None:
    cache_dir = lockfile_with_packages / "pkg_cache"
    lockfile = lockfile_with_packages / "conda.lock"
    packages = collect_bundle_packages(lockfile, [cache_dir])

    output = tmp_path / "bundled.tar.gz"
    config = ArchiveConfig()
    create_archive(lockfile_with_packages, output, config, bundle_packages=packages)

    info = inspect_archive(output)
    assert info["has_manifest"] is True
    assert info["has_lockfile"] is True
    assert info["has_packages"] is True
    assert info["package_count"] == 2


def test_inspect_archive_not_workspace(tmp_path: Path) -> None:
    archive = tmp_path / "random.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo(name="readme.txt")
        info.size = 5
        tf.addfile(info, io.BytesIO(b"hello"))

    result = inspect_archive(archive)
    assert result["has_manifest"] is False
