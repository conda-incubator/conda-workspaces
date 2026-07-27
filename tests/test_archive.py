from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

import pytest
from conda.base.context import context as conda_context

from conda_workspaces.archive import (
    ALLOWED_TAR_TYPES,
    WorkspaceArchive,
    add_files_to_tar,
    collect_archive_files,
    collect_bundle_packages,
    create_archive,
    extract_archive,
    inspect_archive,
    open_tar,
    parse_relative_archive_path,
    url_to_filename,
    validate_tar_member,
    validate_tar_members,
    verify_package_hashes,
)
from conda_workspaces.exceptions import (
    ArchiveError,
    ArchiveHashMismatchError,
    ArchivePathTraversalError,
)
from conda_workspaces.models import ArchiveConfig
from conda_workspaces.receipts import ArchiveReceipt

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import SnapshotTree


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
        cwd=project_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "conda.toml", "conda.lock", "src/main.py"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )
    return project_dir


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


@pytest.fixture
def bundled_archive(lockfile_with_packages: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Create a bundled archive from lockfile_with_packages, return (archive, root)."""
    cache_dir = lockfile_with_packages / "pkg_cache"
    lockfile = lockfile_with_packages / "conda.lock"
    packages = collect_bundle_packages(lockfile, [cache_dir])
    output = tmp_path / "bundled.tar.gz"
    config = ArchiveConfig()
    create_archive(lockfile_with_packages, output, config, bundle_packages=packages)
    return output, lockfile_with_packages


@pytest.fixture
def workspace_archive_project(tmp_path: Path) -> Path:
    """Create a workspace that can be archived through the public API."""
    platform = conda_context.subdir
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "conda.toml").write_text(
        f"""\
[workspace]
name = "archive-api-test"
channels = ["conda-forge"]
platforms = ["{platform}"]
""",
        encoding="utf-8",
    )
    (root / "conda.lock").write_text(
        f"""\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      {platform}: []
packages: []
""",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    return root


@pytest.fixture
def receipt_bundled_archive_factory(
    tmp_path: Path,
) -> Callable[[str, str, bytes, str | None], WorkspaceArchive]:
    """Build receipt-backed archives with one bundled package."""

    def build(
        label: str,
        package_name: str,
        package_content: bytes,
        manifest_dependency: str | None = None,
    ) -> WorkspaceArchive:
        root = tmp_path / label
        root.mkdir()
        dependency = (
            f'\n[dependencies]\n{manifest_dependency} = ">=1"\n'
            if manifest_dependency is not None
            else ""
        )
        (root / "conda.toml").write_text(
            f"""\
[workspace]
name = "{label}"
channels = ["conda-forge"]
platforms = ["{conda_context.subdir}"]
{dependency}""",
            encoding="utf-8",
        )
        package_url = (
            "https://conda.anaconda.org/conda-forge/"
            f"{conda_context.subdir}/{package_name}"
        )
        (root / "conda.lock").write_text(
            f"""\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      {conda_context.subdir}:
        - conda: {package_url}
packages:
  - conda: {package_url}
    sha256: {hashlib.sha256(package_content).hexdigest()}
    name: {label}
    version: "1.0"
    build: h0
    subdir: {conda_context.subdir}
    depends: []
""",
            encoding="utf-8",
        )
        package = tmp_path / f"{label}-cache" / package_name
        package.parent.mkdir()
        package.write_bytes(package_content)
        archive_path = tmp_path / f"{label}.tar.gz"
        archive_config = ArchiveConfig()
        create_archive(
            root,
            archive_path,
            archive_config,
            bundle_packages=[package],
        )
        receipt_path = ArchiveReceipt.default_path(archive_path)
        ArchiveReceipt.build(
            root=root,
            archive_path=archive_path,
            archive_config=archive_config,
            manifest_path=root / "conda.toml",
            lockfile_path=root / "conda.lock",
            environment_prefixes={"default": ".conda/envs/default"},
            options={"bundle": True, "lock": False},
        ).write(receipt_path)
        return WorkspaceArchive(archive_path, receipt=receipt_path)

    return build


def test_collect_files_git_tracked(git_project: Path) -> None:
    config = ArchiveConfig()
    files = collect_archive_files(git_project, config)
    rel_paths = {f.relative_to(git_project).as_posix() for f in files}
    assert "conda.toml" in rel_paths
    assert "conda.lock" in rel_paths
    assert "src/main.py" in rel_paths
    assert ".env" not in rel_paths
    assert "data/big.bin" not in rel_paths


def test_collect_files_non_git(project_dir: Path) -> None:
    config = ArchiveConfig()
    files = collect_archive_files(project_dir, config)
    rel_paths = {f.relative_to(project_dir).as_posix() for f in files}
    assert "conda.toml" in rel_paths
    assert "src/main.py" in rel_paths
    assert "data/big.bin" in rel_paths
    assert ".env" not in rel_paths


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env",
        ".env.production",
        ".env.local",
        ".env.production.local",
        ".aws/credentials",
        ".azure/msal_token_cache.json",
        ".config/gcloud/application_default_credentials.json",
        ".condarc",
        ".docker/config.json",
        ".git-credentials",
        ".gnupg/private-keys-v1.d/key",
        ".kube/config",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".ssh/id_rsa",
        ".terraform/terraform.tfstate",
        "app.key",
        "credentials.p12",
        "credentials.pfx",
        "credentials.pem",
        "identity.jks",
        "identity.keystore",
        "kubeconfig",
        "secret.secret",
        "secrets",
        "secrets.yaml",
        "terraform.tfstate",
        "terraform.tfstate.backup",
        "nested/.env",
        "nested/.ssh/id_ed25519",
        "nested/secrets/token.txt",
    ],
    ids=[
        "dotenv",
        "dotenv-environment",
        "dotenv-local",
        "dotenv-env-local",
        "aws-credentials",
        "azure-config",
        "gcloud-config",
        "condarc",
        "docker-config",
        "git-credentials",
        "gnupg",
        "kube-config",
        "netrc",
        "npmrc",
        "pypirc",
        "ssh-key",
        "terraform-dir",
        "key-file",
        "p12-file",
        "pfx-file",
        "pem-file",
        "jks-file",
        "keystore-file",
        "kubeconfig",
        "secret-extension",
        "secrets-file",
        "secrets-yaml",
        "terraform-state",
        "terraform-state-backup",
        "nested-dotenv",
        "nested-ssh-key",
        "nested-secrets-dir",
    ],
)
def test_collect_files_excludes_default_sensitive_files(
    project_dir: Path,
    relative_path: str,
) -> None:
    path = project_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SECRET=abc\n", encoding="utf-8")

    config = ArchiveConfig()
    files = collect_archive_files(project_dir, config)
    rel_paths = {f.relative_to(project_dir).as_posix() for f in files}

    assert relative_path not in rel_paths
    assert "conda.toml" in rel_paths


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env.dist",
        ".env.example",
        ".env.sample",
        ".env.template",
        "docs/secrets-guide.md",
        "nested/.env.example",
        "nested/id_rsa.pub",
    ],
    ids=[
        "dotenv-dist",
        "dotenv-example",
        "dotenv-sample",
        "dotenv-template",
        "secrets-doc",
        "nested-dotenv-example",
        "nested-public-key",
    ],
)
def test_collect_files_keeps_safe_examples(
    project_dir: Path,
    relative_path: str,
) -> None:
    path = project_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("TOKEN=\n", encoding="utf-8")

    config = ArchiveConfig()
    files = collect_archive_files(project_dir, config)
    rel_paths = {f.relative_to(project_dir).as_posix() for f in files}

    assert relative_path in rel_paths


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
    rel_strs = {f.relative_to(project_dir).as_posix() for f in files}

    assert not any(p.startswith(".git/") or p == ".git" for p in rel_strs)
    assert not any(p.startswith(".conda/envs") for p in rel_strs)
    assert not any(p.startswith(".pixi/") for p in rel_strs)


def test_collect_files_include_filter(project_dir: Path) -> None:
    config = ArchiveConfig(include=("src/**",))
    files = collect_archive_files(project_dir, config)
    rel_paths = {f.relative_to(project_dir).as_posix() for f in files}
    assert "src/main.py" in rel_paths
    assert "conda.toml" not in rel_paths


def test_collect_files_include_and_exclude(project_dir: Path) -> None:
    """Include narrows, then exclude removes from that set."""
    config = ArchiveConfig(include=("src/**", "conda.toml"), exclude=("src/main.py",))
    files = collect_archive_files(project_dir, config)
    rel_paths = {f.relative_to(project_dir).as_posix() for f in files}
    assert "conda.toml" in rel_paths
    assert "src/main.py" not in rel_paths


def test_collect_files_custom_exclude(project_dir: Path) -> None:
    config = ArchiveConfig(exclude=("data/**",))
    files = collect_archive_files(project_dir, config)
    rel_paths = {f.relative_to(project_dir).as_posix() for f in files}
    assert "data/big.bin" not in rel_paths
    assert "conda.toml" in rel_paths


@pytest.mark.parametrize("suffix", [".tar.gz", ".tar.zst", ".tar.bz2"])
def test_create_archive(project_dir: Path, tmp_path: Path, suffix: str) -> None:
    (project_dir / ".npmrc").write_text("//registry.example/:_authToken=secret\n")
    (project_dir / ".ssh").mkdir()
    (project_dir / ".ssh" / "id_rsa").write_text("secret\n")

    output = tmp_path / "out" / f"project{suffix}"
    config = ArchiveConfig()
    create_archive(project_dir, output, config)

    assert output.is_file()
    with open_tar(output) as tf:
        names = tf.getnames()
    assert "conda.toml" in names
    assert "conda.lock" in names
    assert "src/main.py" in names
    assert ".env" not in names
    assert ".npmrc" not in names
    assert ".ssh/id_rsa" not in names


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


def test_add_files_to_tar_writes_posix_member_names() -> None:
    class RecordingTar:
        def __init__(self) -> None:
            self.arcnames: list[str] = []

        def add(self, name: str, arcname: str) -> None:
            self.arcnames.append(arcname)

    tf = RecordingTar()

    add_files_to_tar(
        tf,
        PureWindowsPath("C:/workspace"),
        [PureWindowsPath("C:/workspace/src/main.py")],
    )

    assert tf.arcnames == ["src/main.py"]


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


def test_extract_archive_allows_existing_empty_target(
    project_dir: Path,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "test.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, archive_path, config)
    target = tmp_path / "extracted"
    target.mkdir()

    result = extract_archive(archive_path, target)

    assert result == target
    assert (target / "conda.toml").is_file()


@pytest.mark.parametrize(
    "target_setup",
    ["non-empty", "file-target", "symlink-target"],
    ids=["non-empty", "file-target", "symlink-target"],
)
def test_extract_archive_rejects_existing_target(
    project_dir: Path,
    tmp_path: Path,
    existing_extract_target: Callable[[str], Path],
    target_setup: str,
) -> None:
    archive_path = tmp_path / "test.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, archive_path, config)
    target = existing_extract_target(target_setup)

    with pytest.raises(ArchiveError, match="Cannot extract archive"):
        extract_archive(archive_path, target)

    if target_setup == "non-empty":
        assert (target / "conda.toml").read_text(encoding="utf-8") == "trusted = true\n"
    elif target_setup == "file-target":
        assert target.read_text(encoding="utf-8") == "trusted file\n"


@pytest.mark.parametrize(
    ("name", "link_type", "linkname"),
    [
        pytest.param("../../../etc/passwd", None, None, id="dotdot-traversal"),
        pytest.param("/tmp/evil_file", None, None, id="absolute-path"),
        pytest.param("C:/tmp/evil_file", None, None, id="windows-drive"),
        pytest.param("dir\\evil_file", None, None, id="windows-backslash"),
        pytest.param("escape", tarfile.SYMTYPE, "../../../etc", id="symlink-escape"),
    ],
)
def test_extract_archive_path_traversal_blocked(
    tmp_path: Path,
    name: str,
    link_type: int | None,
    linkname: str | None,
) -> None:
    evil_archive = tmp_path / "evil.tar.gz"
    with tarfile.open(evil_archive, "w:gz") as tf:
        info = tarfile.TarInfo(name=name)
        if link_type is not None:
            info.type = link_type
            info.linkname = linkname
        else:
            info.size = 4
        tf.addfile(info, io.BytesIO(b"evil") if info.size else None)

    target = tmp_path / "safe"
    with pytest.raises(ArchivePathTraversalError):
        extract_archive(evil_archive, target)


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (["path", "path"], "duplicate member"),
        (["path", "path/child"], "nested under"),
        (["path/child", "path"], "conflicts with nested member"),
    ],
    ids=["duplicate", "parent-first", "child-first"],
)
def test_validate_tar_members_rejects_conflicting_topology(
    names: list[str],
    message: str,
) -> None:
    members = [tarfile.TarInfo(name) for name in names]

    with pytest.raises(ArchiveError, match=message):
        validate_tar_members(members)


def test_workspace_archive_dry_run_rejects_invalid_member_topology(
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        manifest = b"[workspace]\nname = 'unsafe'\n"
        manifest_info = tarfile.TarInfo("conda.toml")
        manifest_info.size = len(manifest)
        tar.addfile(manifest_info, io.BytesIO(manifest))
        link = tarfile.TarInfo("dangling-hardlink")
        link.type = tarfile.LNKTYPE
        link.linkname = "missing"
        tar.addfile(link)
    before = snapshot_tree(tmp_path)

    with pytest.raises(ArchiveError):
        WorkspaceArchive(archive).extract(
            target=tmp_path / "target",
            dry_run=True,
        )

    assert snapshot_tree(tmp_path) == before


@pytest.mark.parametrize(
    ("path", "allow_parent"),
    [
        ("conda.toml", False),
        ("envs/default/conda-meta/history", False),
        ("../target", True),
    ],
    ids=["file", "nested", "link-parent"],
)
def test_parse_relative_archive_path_allows_valid_paths(
    path: str,
    allow_parent: bool,
) -> None:
    assert parse_relative_archive_path(path, allow_parent=allow_parent).as_posix()


@pytest.mark.parametrize(
    ("path", "allow_parent"),
    [
        ("", False),
        ("/tmp/evil", False),
        ("C:/tmp/evil", False),
        ("dir\\evil", False),
        ("dir/../evil", False),
        ("dir/./evil", False),
        ("dir//evil", False),
        ("bad\0path", False),
        ("C:evil", True),
    ],
    ids=[
        "empty",
        "absolute",
        "windows-drive",
        "backslash",
        "parent",
        "current-dir",
        "double-slash",
        "nul",
        "drive-relative-link",
    ],
)
def test_parse_relative_archive_path_rejects_unsafe_paths(
    path: str,
    allow_parent: bool,
) -> None:
    with pytest.raises(ValueError):
        parse_relative_archive_path(path, allow_parent=allow_parent)


def test_extract_archive_zst(project_dir: Path, tmp_path: Path) -> None:
    archive_path = tmp_path / "test.tar.zst"
    config = ArchiveConfig()
    create_archive(project_dir, archive_path, config)

    target = tmp_path / "extracted"
    extract_archive(archive_path, target)

    assert (target / "conda.toml").is_file()
    assert (target / "src" / "main.py").is_file()


def test_collect_bundle_packages_missing(
    lockfile_with_packages: Path,
) -> None:
    empty_cache = lockfile_with_packages / "empty_cache"
    empty_cache.mkdir()
    lockfile = lockfile_with_packages / "conda.lock"
    with pytest.raises(ArchiveError, match="not found in cache"):
        collect_bundle_packages(lockfile, [empty_cache])


def test_collect_bundle_packages(lockfile_with_packages: Path) -> None:
    cache_dir = lockfile_with_packages / "pkg_cache"
    lockfile = lockfile_with_packages / "conda.lock"
    packages = collect_bundle_packages(lockfile, [cache_dir])
    assert len(packages) == 2
    filenames = {p.name for p in packages}
    assert "zlib-1.2.13-h4dc568a_6.conda" in filenames
    assert "zlib-1.2.13-h53f4e23_6.conda" in filenames


def test_collect_bundle_packages_rejects_filename_collision(project_dir: Path) -> None:
    """Flat archive bundles must not silently collapse package filenames."""
    lockfile_content = """\
version: 1
packages:
  - conda: https://example.com/channel-a/linux-64/same-1.0-h0.conda
    sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  - conda: https://example.com/channel-b/linux-64/same-1.0-h0.conda
    sha256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""
    lockfile = project_dir / "conda.lock"
    lockfile.write_text(lockfile_content, encoding="utf-8")

    cache_dir = project_dir / "pkg_cache"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "same-1.0-h0.conda").write_bytes(b"package")

    with pytest.raises(ArchiveError, match="filename collision"):
        collect_bundle_packages(lockfile, [cache_dir])


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


def test_create_archive_with_bundle(
    bundled_archive: tuple[Path, Path],
) -> None:
    output, _ = bundled_archive
    with tarfile.open(output, "r:gz") as tf:
        names = tf.getnames()
    assert "packages/zlib-1.2.13-h4dc568a_6.conda" in names
    assert "packages/zlib-1.2.13-h53f4e23_6.conda" in names
    assert "conda.toml" in names


@pytest.mark.parametrize(
    ("member_kind", "member_name", "message"),
    [
        ("nested", "packages/nested/demo-1.0-h0.conda", "direct children"),
        ("symlink", "packages/demo-1.0-h0.conda", "regular file"),
        ("hardlink", "packages/demo-1.0-h0.conda", "regular file"),
    ],
    ids=["nested", "symlink", "hardlink"],
)
def test_workspace_archive_rejects_non_regular_or_nested_bundled_packages(
    tmp_path: Path,
    member_kind: str,
    member_name: str,
    message: str,
) -> None:
    package_content = b"package"
    archive_path = tmp_path / "workspace.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        if member_kind in {"symlink", "hardlink"}:
            payload = tarfile.TarInfo("payload")
            payload.size = len(package_content)
            tar.addfile(payload, io.BytesIO(package_content))
            member = tarfile.TarInfo(member_name)
            member.type = (
                tarfile.SYMTYPE if member_kind == "symlink" else tarfile.LNKTYPE
            )
            member.linkname = payload.name
            tar.addfile(member)
        else:
            member = tarfile.TarInfo(member_name)
            member.size = len(package_content)
            tar.addfile(member, io.BytesIO(package_content))
    with pytest.raises(ArchiveError, match=message):
        inspect_archive(archive_path)


def test_inspect_archive_lightweight(project_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "test.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, output, config)

    info = inspect_archive(output)
    assert info["has_manifest"] is True
    assert info["has_lockfile"] is True
    assert info["has_packages"] is False


def test_inspect_archive_bundled(bundled_archive: tuple[Path, Path]) -> None:
    output, _ = bundled_archive
    info = inspect_archive(output)
    assert info["has_manifest"] is True
    assert info["has_lockfile"] is True
    assert info["has_packages"] is True
    assert info["package_count"] == 2


def test_inspect_archive_counts_legacy_package_archives(tmp_path: Path) -> None:
    archive = tmp_path / "legacy.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        manifest = tarfile.TarInfo(name="conda.toml")
        manifest.size = 0
        tf.addfile(manifest, io.BytesIO(b""))
        package = tarfile.TarInfo(name="packages/legacy-1.0-h123.tar.bz2")
        package.size = 4
        tf.addfile(package, io.BytesIO(b"data"))

    result = inspect_archive(archive)

    assert result["has_packages"] is True
    assert result["package_count"] == 1


def test_inspect_archive_not_workspace(tmp_path: Path) -> None:
    archive = tmp_path / "random.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo(name="readme.txt")
        info.size = 5
        tf.addfile(info, io.BytesIO(b"hello"))

    result = inspect_archive(archive)
    assert result["has_manifest"] is False


def test_inspect_archive_ignores_ambient_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "workspace.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        content = b"safe"
        info = tarfile.TarInfo("linkdir/file.txt")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))

    ambient = tmp_path / "ambient"
    ambient.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (ambient / "linkdir").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(ambient)

    assert inspect_archive(archive)["has_manifest"] is False


def test_workspace_archive_create_writes_receipt(
    workspace_archive_project: Path,
) -> None:
    output = workspace_archive_project / "workspace.tar.gz"

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=output,
        receipt=True,
    )

    assert archive.path == output.resolve()
    assert archive.receipt_path == output.resolve().with_name(
        "workspace.tar.gz.receipt.json"
    )
    assert archive.path.is_file()
    assert archive.receipt_path is not None
    assert archive.receipt_path.is_file()
    assert archive.inspect()["has_manifest"] is True
    assert archive.verify().workspace_paths == ("conda.toml", "conda.lock")


def test_workspace_archive_reads_through_symlink_alias(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    created = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=output,
        receipt=True,
    )
    alias = tmp_path / "alias.tar.gz"
    alias.symlink_to(output)

    archive = WorkspaceArchive(alias, receipt=True)

    assert archive.path == output.resolve()
    assert archive.receipt_path == created.receipt_path
    assert archive.inspect()["has_manifest"] is True
    assert archive.verify().workspace_paths == ("conda.toml", "conda.lock")


def test_workspace_archive_extract_rejects_receipt_bound_manifest_symlink(
    workspace_archive_project: Path,
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
) -> None:
    (workspace_archive_project / "pixi.toml").symlink_to("conda.toml")
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
    )
    assert archive.receipt_path is not None
    statement = json.loads(archive.receipt_path.read_text(encoding="utf-8"))
    statement["predicate"]["workspace"]["manifest"] = "pixi.toml"
    for subject in statement["subject"]:
        if subject["name"] == "conda.toml":
            subject["name"] = "pixi.toml"
    archive.receipt_path.write_text(
        json.dumps(statement),
        encoding="utf-8",
    )
    before = snapshot_tree(tmp_path)

    with pytest.raises(
        ArchiveError,
        match="Receipt workspace manifest is not a regular archive member",
    ):
        archive.extract(
            target=tmp_path / "extracted",
            dry_run=True,
        )

    assert snapshot_tree(tmp_path) == before


@pytest.mark.parametrize(
    ("unsafe_alias", "message"),
    [
        ("manifest-symlink", "symbolic links are not supported"),
        ("archive-symlink", "cannot be a symbolic link"),
        ("receipt-hardlink", "archived workspace input"),
    ],
)
def test_workspace_archive_create_dry_run_rejects_unsafe_aliases(
    workspace_archive_project: Path,
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
    unsafe_alias: str,
    message: str,
) -> None:
    source = workspace_archive_project / "src" / "app.py"
    output = tmp_path / "workspace.tar.gz"
    receipt = None
    if unsafe_alias == "manifest-symlink":
        manifest = workspace_archive_project / "conda.toml"
        target = tmp_path / "outside-manifest"
        manifest.rename(target)
        manifest.symlink_to(target)
    elif unsafe_alias == "archive-symlink":
        output.symlink_to(source)
    else:
        receipt = workspace_archive_project / "receipt.json"
        receipt.hardlink_to(source)
    before = snapshot_tree(tmp_path)

    with pytest.raises(ArchiveError, match=message):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=receipt,
            dry_run=True,
        )

    assert snapshot_tree(tmp_path) == before
    assert source.read_text(encoding="utf-8") == "print('hello')\n"


@pytest.mark.parametrize(
    "alias_kind",
    ["direct", "symlink", "hardlink"],
    ids=["direct", "symlink", "hardlink"],
)
def test_create_archive_rejects_output_colliding_with_bundled_package(
    project_dir: Path,
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
    alias_kind: str,
) -> None:
    package = tmp_path / "cache" / "demo-1.0-h0.conda"
    package.parent.mkdir()
    package.write_bytes(b"package")
    if alias_kind == "direct":
        output = package
    else:
        output = tmp_path / "workspace.tar.gz"
        if alias_kind == "symlink":
            output.symlink_to(package)
        else:
            output.hardlink_to(package)
    before = snapshot_tree(tmp_path)

    with pytest.raises(
        ArchiveError,
        match=(
            "cannot be a symbolic link"
            if alias_kind == "symlink"
            else "Archive output cannot overwrite a bundled package input"
        ),
    ):
        create_archive(
            project_dir,
            output,
            ArchiveConfig(),
            bundle_packages=[package],
        )

    assert snapshot_tree(tmp_path) == before
    assert package.read_bytes() == b"package"


def test_workspace_archive_create_refreshes_manifest_caches(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> None:
    secret = workspace_archive_project / "secret.txt"
    secret.write_text("keep out\n", encoding="utf-8")
    first = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "first.tar.gz",
    )
    manifest = workspace_archive_project / "conda.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[workspace.archive]\nexclude = ["secret.txt"]\n',
        encoding="utf-8",
    )

    second = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "second.tar.gz",
    )

    with open_tar(first.path) as tar:
        assert "secret.txt" in tar.getnames()
    with open_tar(second.path) as tar:
        assert "secret.txt" not in tar.getnames()


def test_workspace_archive_create_refreshes_manifest_selection(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> None:
    WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "first.tar.gz",
    )
    (workspace_archive_project / "conda.toml").write_text(
        "[project]\nname = 'not-a-workspace'\n",
        encoding="utf-8",
    )
    (workspace_archive_project / "pixi.toml").write_text(
        f"""\
[workspace]
name = "selected-pixi"
channels = ["conda-forge"]
platforms = ["{conda_context.subdir}"]
""",
        encoding="utf-8",
    )

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "second.tar.gz",
    )

    result = archive.extract(target=tmp_path / "extracted", prime_cache=False)
    assert (
        WorkspaceArchive.resolve_extracted_manifest(result.target).name == "pixi.toml"
    )


def test_workspace_archive_lock_includes_generated_lock_in_git_repo(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lockfile = workspace_archive_project / "conda.lock"
    lockfile.unlink()
    subprocess.run(
        ["git", "init"],
        cwd=workspace_archive_project,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "conda.toml", "src/app.py"],
        cwd=workspace_archive_project,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(
        "conda_workspaces.resolver.resolve_all_environments",
        lambda config, platform: {"default": object()},
    )
    monkeypatch.setattr(
        "conda_workspaces.lockfile.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: (
            """\
version: 1
environments:
  default:
    channels: []
    packages:
      linux-64: []
packages: []
"""
        ),
    )

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        lock=True,
        receipt=True,
    )

    with open_tar(archive.path) as tar:
        assert "conda.lock" in tar.getnames()
    result = archive.extract(target=tmp_path / "extracted")
    assert result.verified is True
    assert (result.target / "conda.lock").is_file()


@pytest.mark.parametrize("dry_run", [False, True], ids=["create", "dry-run"])
def test_workspace_archive_lock_preflights_before_writing_generated_lock(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    dry_run: bool,
) -> None:
    lockfile = workspace_archive_project / "conda.lock"
    lockfile.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (workspace_archive_project / "outside-link").symlink_to(Path("..") / outside.name)
    monkeypatch.setattr(
        "conda_workspaces.resolver.resolve_all_environments",
        lambda config, platform: {"default": object()},
    )
    monkeypatch.setattr(
        "conda_workspaces.lockfile.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: (
            f"""\
version: 1
environments:
  default:
    channels: []
    packages:
      {conda_context.subdir}: []
packages: []
"""
        ),
    )
    output = tmp_path / "workspace.tar.gz"
    before = snapshot_tree(tmp_path)

    with pytest.raises(ArchivePathTraversalError):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            lock=True,
            dry_run=dry_run,
        )

    assert snapshot_tree(tmp_path) == before
    assert not lockfile.exists()
    assert not output.exists()


def test_workspace_archive_extract_uses_receipt(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
    )

    result = archive.extract(
        target=tmp_path / "extracted",
        require_sha256=True,
    )

    assert result.target == (tmp_path / "extracted").resolve()
    assert result.verified is True
    assert result.receipt_path == archive.receipt_path
    assert (result.target / "src" / "app.py").is_file()


def test_workspace_archive_extract_preserves_empty_target_metadata(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    target = tmp_path / "extracted"
    target.mkdir(mode=0o700)
    os.utime(target, ns=(1_700_000_000_123_456_789, 1_700_000_001_987_654_321))
    expected = target.stat()
    monkeypatch.setattr(os, "supports_follow_symlinks", set())

    archive.extract(target=target)

    actual = target.stat()
    assert actual.st_ino == expected.st_ino
    assert actual.st_uid == expected.st_uid
    assert actual.st_gid == expected.st_gid
    assert actual.st_mode == expected.st_mode
    assert actual.st_atime_ns == expected.st_atime_ns
    assert actual.st_mtime_ns == expected.st_mtime_ns
    assert (target / "src" / "app.py").is_file()


def test_workspace_archive_extract_rolls_back_empty_target_promotion(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    target = tmp_path / "extracted"
    target.mkdir(mode=0o700)
    os.utime(target, ns=(1_700_000_000_123_456_789, 1_700_000_001_987_654_321))
    expected = target.stat()
    rename = Path.rename
    calls = 0

    def fail_second_staged_rename(path: Path, destination: Path) -> Path:
        nonlocal calls
        if path.parent.name == "workspace":
            calls += 1
            if calls == 2:
                raise OSError("promotion failed")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_second_staged_rename)

    with pytest.raises(OSError, match="promotion failed"):
        archive.extract(target=target)

    actual = target.stat()
    assert actual.st_ino == expected.st_ino
    assert actual.st_uid == expected.st_uid
    assert actual.st_gid == expected.st_gid
    assert actual.st_mode == expected.st_mode
    assert actual.st_atime_ns == expected.st_atime_ns
    assert actual.st_mtime_ns == expected.st_mtime_ns
    assert not any(target.iterdir())


@pytest.mark.parametrize("target_existed", [False, True], ids=["absent", "empty"])
def test_workspace_archive_extract_rejects_target_swap(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_existed: bool,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    target = tmp_path / "extracted"
    if target_existed:
        target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    extract = extract_archive

    def swap_target(archive_path: Path, staged: Path) -> Path:
        result = extract(archive_path, staged)
        if target.exists():
            target.rmdir()
        target.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr("conda_workspaces.archive.extract_archive", swap_target)

    with pytest.raises(ArchiveError, match="target changed"):
        archive.extract(target=target)

    assert target.is_symlink()
    assert not any(outside.iterdir())


def test_workspace_archive_receipt_refreshes_lockfile_cache(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> None:
    WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "first.tar.gz",
        receipt=True,
    )
    package_name = "demo-1.0-h0.conda"
    package_url = (
        f"https://conda.anaconda.org/conda-forge/{conda_context.subdir}/{package_name}"
    )
    (workspace_archive_project / "conda.lock").write_text(
        f"""\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      {conda_context.subdir}:
        - conda: {package_url}
packages:
  - conda: {package_url}
    sha256: {"a" * 64}
    name: demo
    version: "1.0"
    build: h0
    subdir: {conda_context.subdir}
    depends: []
""",
        encoding="utf-8",
    )

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "second.tar.gz",
        receipt=True,
    )
    result = archive.extract(
        target=tmp_path / "extracted",
        prime_cache=False,
    )

    assert result.verified is True
    assert package_name in (result.target / "conda.lock").read_text(encoding="utf-8")


def test_workspace_archive_extract_refreshes_same_target_lock_cache(
    tmp_path: Path,
    receipt_bundled_archive_factory: Callable[
        [str, str, bytes, str | None], WorkspaceArchive
    ],
) -> None:
    packages = [
        ("first", "python-1.0-h0.conda", b"python package"),
        ("second", "numpy-2.0-h0.conda", b"numpy package"),
    ]
    archives = [
        receipt_bundled_archive_factory(label, name, content, None)
        for label, name, content in packages
    ]

    target = tmp_path / "extracted"
    package_cache = tmp_path / "package-cache"
    first = archives[0].extract(target=target, package_cache=package_cache)
    shutil.rmtree(target)
    second = archives[1].extract(target=target, package_cache=package_cache)

    assert first.primed_packages == 1
    assert second.primed_packages == 1
    assert {path.name for path in package_cache.iterdir()} == {
        package_name for _, package_name, _ in packages
    }


@pytest.mark.parametrize("dry_run", [False, True], ids=["extract", "dry-run"])
@pytest.mark.parametrize("cache_entry", ["corrupt", "symlink"])
def test_workspace_archive_extract_rejects_invalid_cached_packages(
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
    receipt_bundled_archive_factory: Callable[
        [str, str, bytes, str | None], WorkspaceArchive
    ],
    dry_run: bool,
    cache_entry: str,
) -> None:
    package_name = "demo-1.0-h0.conda"
    archive = receipt_bundled_archive_factory(
        "valid",
        package_name,
        b"package",
        None,
    )
    cache = tmp_path / "package-cache"
    cache.mkdir()
    destination = cache / package_name
    if cache_entry == "corrupt":
        destination.write_bytes(b"corrupt")
    else:
        destination.symlink_to(tmp_path / "outside-package")
    before = snapshot_tree(tmp_path)

    with pytest.raises(ArchiveError):
        archive.extract(
            target=tmp_path / "extracted",
            package_cache=cache,
            dry_run=dry_run,
        )

    assert snapshot_tree(tmp_path) == before


@pytest.mark.parametrize(
    ("runtime_prefix", "dest", "expected_staged_prefix", "expected_runtime_prefix"),
    [
        ("tmp-runtime", None, Path("direct-runtime"), None),
        (
            "/opt/runtime",
            "rootfs",
            Path("rootfs") / "opt" / "runtime",
            "/opt/runtime",
        ),
    ],
    ids=["direct-prefix", "staged-dest"],
)
def test_workspace_archive_install_uses_public_handler(
    workspace_archive_project: Path,
    tmp_path: Path,
    runtime_prefix: str,
    dest: str | None,
    expected_staged_prefix: Path,
    expected_runtime_prefix: str | None,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    calls: list[tuple[Path, str | None, Path | None, str | None]] = []

    def install_handler(
        workspace: Path,
        environment: str | None,
        install_prefix: Path | None,
        target_prefix_override: str | None,
    ) -> int:
        calls.append((workspace, environment, install_prefix, target_prefix_override))
        if install_prefix is not None:
            install_prefix.mkdir(parents=True, exist_ok=True)
            (install_prefix / "prefix.txt").write_text(
                str(install_prefix),
                encoding="utf-8",
            )
        return 0

    dest_path = tmp_path / dest if dest is not None else None
    prefix = (
        str(tmp_path / "direct-runtime")
        if runtime_prefix == "tmp-runtime"
        else runtime_prefix
    )
    result = archive.install(
        target=tmp_path / "extracted",
        environment="default",
        prefix=prefix,
        dest=dest_path,
        install_handler=install_handler,
    )

    resolved_install_prefix = tmp_path / expected_staged_prefix
    assert calls == [
        (
            (tmp_path / "extracted").resolve(),
            "default",
            resolved_install_prefix,
            expected_runtime_prefix,
        )
    ]
    assert result.return_code == 0
    assert result.install_prefix == resolved_install_prefix
    assert result.runtime_prefix == expected_runtime_prefix
    if expected_runtime_prefix is None:
        assert result.prefix_reference_matches == ()
    else:
        assert result.prefix_reference_matches == (
            resolved_install_prefix / "prefix.txt",
        )


def test_workspace_archive_install_validates_manifest_before_public_handler(
    workspace_archive_project: Path,
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
) -> None:
    (workspace_archive_project / "pixi.toml").write_text(
        "[workspace]\nname = 'ambiguous'\n",
        encoding="utf-8",
    )
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    calls: list[Path] = []
    before = snapshot_tree(tmp_path)

    with pytest.raises(ArchiveError, match="multiple workspace manifests"):
        archive.install(
            target=tmp_path / "extracted",
            install_handler=lambda workspace, *_: calls.append(workspace) or 0,
            dry_run=True,
        )

    assert calls == []
    assert snapshot_tree(tmp_path) == before


@pytest.mark.parametrize(
    ("root_manifests", "expected"),
    [
        ({"conda.toml": "not a workspace\n"}, "no valid workspace manifest"),
        (
            {
                "conda.toml": "[workspace]\nname = 'conda'\n",
                "pixi.toml": "[workspace]\nname = 'pixi'\n",
            },
            "multiple workspace manifests",
        ),
    ],
    ids=["invalid-root", "ambiguous-root"],
)
def test_resolve_extracted_manifest_stays_at_archive_root(
    tmp_path: Path,
    root_manifests: dict[str, str],
    expected: str,
) -> None:
    (tmp_path / "conda.toml").write_text(
        "[workspace]\nname = 'parent'\n",
        encoding="utf-8",
    )
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    for filename, content in root_manifests.items():
        (extracted / filename).write_text(content, encoding="utf-8")

    with pytest.raises(ArchiveError, match=expected):
        WorkspaceArchive.resolve_extracted_manifest(extracted)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"prefix": "/opt/runtime"}, "--prefix requires an explicit environment"),
        (
            {"environment": "default", "prefix": "relative/runtime"},
            "--prefix must be an absolute path",
        ),
        (
            {"environment": "default", "dest": "rootfs"},
            "--dest requires --prefix",
        ),
    ],
    ids=["prefix-without-env", "relative-prefix", "dest-without-prefix"],
)
def test_workspace_archive_install_rejects_invalid_prefix_options(
    workspace_archive_project: Path,
    tmp_path: Path,
    kwargs: dict[str, str],
    message: str,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )

    with pytest.raises(ArchiveError, match=message):
        archive.install(target=tmp_path / "extracted", **kwargs)


def test_archive_roundtrip(git_project: Path, tmp_path: Path) -> None:
    """Full round-trip: create archive, extract, verify contents match."""
    config = ArchiveConfig()
    archive_path = tmp_path / "roundtrip.tar.gz"
    create_archive(git_project, archive_path, config)

    target = tmp_path / "extracted"
    extract_archive(archive_path, target)

    assert (target / "conda.toml").read_text() == (
        git_project / "conda.toml"
    ).read_text()
    assert (target / "conda.lock").read_text() == (
        git_project / "conda.lock"
    ).read_text()
    assert (target / "src" / "main.py").read_text() == (
        git_project / "src" / "main.py"
    ).read_text()

    assert not (target / ".env").exists()
    assert not (target / "data").exists()


@pytest.mark.parametrize(
    "file_type",
    [
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.FIFOTYPE,
    ],
    ids=["char-device", "block-device", "fifo"],
)
def test_validate_tar_member_rejects_special_file_types(
    tmp_path: Path, file_type: bytes
) -> None:
    member = tarfile.TarInfo(name="evil_device")
    member.type = file_type
    with pytest.raises(ArchivePathTraversalError):
        validate_tar_member(member, tmp_path)


def test_validate_tar_member_allows_regular_types(tmp_path: Path) -> None:
    for file_type in ALLOWED_TAR_TYPES:
        member = tarfile.TarInfo(name="normal_file")
        member.type = file_type
        if file_type in {tarfile.LNKTYPE, tarfile.SYMTYPE}:
            member.linkname = "normal_target"
        validate_tar_member(member, tmp_path)


def test_verify_package_hashes_rejects_missing_hash(tmp_path: Path) -> None:
    pkg = tmp_path / "nohash-1.0-h000.conda"
    pkg.write_bytes(b"data")

    lockfile_content = """\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/linux-64/nohash-1.0-h000.conda
packages:
  - conda: https://conda.anaconda.org/conda-forge/linux-64/nohash-1.0-h000.conda
    name: nohash
    version: "1.0"
    build: h000
    subdir: linux-64
    depends: []
"""
    lockfile = tmp_path / "conda.lock"
    lockfile.write_text(lockfile_content, encoding="utf-8")

    with pytest.raises(ArchiveError, match="Cannot verify bundled package"):
        verify_package_hashes([pkg], lockfile)


@pytest.mark.parametrize(
    ("url", "filename"),
    [
        (
            "https://example.com/linux-64/pkg-1.0-h0.conda?token=abc",
            "pkg-1.0-h0.conda",
        ),
        ("https://example.com/linux-64/pkg-1.0-h0.tar.bz2", "pkg-1.0-h0.tar.bz2"),
    ],
    ids=["conda-with-query", "tar-bz2"],
)
def test_url_to_filename(url: str, filename: str) -> None:
    assert url_to_filename(url) == filename


def test_url_to_filename_rejects_non_package_url() -> None:
    with pytest.raises(ArchiveError, match="Cannot determine"):
        url_to_filename("https://example.com/linux-64/repodata.json")
