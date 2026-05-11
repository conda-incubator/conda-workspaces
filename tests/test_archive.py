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
