from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

import pytest
from conda.base.context import context as conda_context
from conda.core.package_cache_data import PackageCacheData
from conda_sigstore.evidence import SignerIdentity
from conda_sigstore.statements import InTotoStatement
from conda_sigstore.verification import VerifiedStatement

import conda_workspaces.archive as archive_module
import conda_workspaces.attestations as attestations_module
import conda_workspaces.lockfile as lockfile_module
import conda_workspaces.paths as paths_module
from conda_workspaces.archive import (
    ALLOWED_TAR_TYPES,
    WorkspaceArchive,
    add_files_to_tar,
    collect_archive_files,
    collect_bundle_packages,
    create_archive,
    extract_archive,
    file_contains_bytes,
    inspect_archive,
    open_tar,
    parse_relative_archive_path,
    read_tar_members,
    url_to_filename,
    validate_tar_member,
    validate_tar_members,
    verify_package_hashes,
)
from conda_workspaces.attestations import SignerPolicy
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.exceptions import (
    ArchiveError,
    ArchiveHashMismatchError,
    ArchivePathTraversalError,
    AttestationError,
    CondaWorkspacesError,
    FileRecoveryError,
)
from conda_workspaces.manifests import detect_and_parse
from conda_workspaces.models import ArchiveConfig
from conda_workspaces.receipts import ArchiveReceipt

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

    from conda_workspaces.receipts import VerifiedArchiveWorkspace
    from tests.conftest import SnapshotTree


def verified_receipt(
    payload: bytes,
    signer: SignerIdentity,
) -> VerifiedStatement:
    """Return cryptographic evidence without invoking the Sigstore service."""
    return VerifiedStatement(
        statement=InTotoStatement.from_payload(payload),
        payload=payload,
        signer=signer,
        timestamps=(),
    )


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


@pytest.mark.skipif(os.name != "nt", reason="Windows stat semantics")
@pytest.mark.parametrize("follow_symlinks", [True, False], ids=["stat", "lstat"])
def test_file_generation_matches_windows_file_descriptor(
    tmp_path: Path,
    follow_symlinks: bool,
) -> None:
    path = tmp_path / "input.txt"
    path.write_text("content", encoding="utf-8")
    path_stat = path.stat() if follow_symlinks else path.lstat()

    with path.open("rb") as stream:
        descriptor_stat = os.fstat(stream.fileno())

    assert archive_module.file_generation(path_stat) == archive_module.file_generation(
        descriptor_stat
    )
    assert archive_module.file_generation(path_stat)[-1] == getattr(
        path_stat,
        "st_birthtime_ns",
        path_stat.st_ctime_ns,
    )


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
def signed_receipt_archive(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> tuple[WorkspaceArchive, bytes]:
    """Create an archive with a placeholder bundle for its exact receipt."""
    created = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "signed-workspace.tar.gz",
        receipt=True,
    )
    assert created.receipt_path is not None
    payload = created.receipt_path.read_bytes()
    created.receipt_path.unlink()
    attestation_path = WorkspaceArchive.default_attestation_path(created.path)
    attestation_path.write_bytes(b'{"bundle": true}\n')
    return WorkspaceArchive(created.path, attestation=True), payload


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


@pytest.fixture
def installable_bundled_archive(
    receipt_bundled_archive_factory: Callable[
        [str, str, bytes, str | None], WorkspaceArchive
    ],
) -> WorkspaceArchive:
    """Build a receipt-backed bundle containing one valid conda package."""
    package = io.BytesIO()
    index = json.dumps(
        {
            "name": "offline-demo",
            "version": "1.0",
            "build": "h0",
            "build_number": 0,
            "subdir": conda_context.subdir,
            "depends": [],
        }
    ).encode()
    files = b"share/offline-demo.txt\n"
    payload = b"installed offline\n"
    with tarfile.open(fileobj=package, mode="w:bz2") as tar:
        for name, content in (
            ("info/index.json", index),
            ("info/files", files),
            ("share/offline-demo.txt", payload),
        ):
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    return receipt_bundled_archive_factory(
        "offline-demo",
        "offline-demo-1.0-h0.tar.bz2",
        package.getvalue(),
        "offline-demo",
    )


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
        ".ENV",
        ".AWS/credentials",
        "Secrets/token.txt",
        ".conda/WORKSPACE.LOCK",
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
        "uppercase-dotenv",
        "uppercase-aws",
        "uppercase-secrets-dir",
        "uppercase-publication-lock",
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


def test_add_files_to_tar_writes_posix_member_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingTar:
        def __init__(self) -> None:
            self.arcnames: list[str] = []

    tf = RecordingTar()

    def record_arcname(
        _tf: object,
        _path: object,
        arcname: str,
        **_kwargs: object,
    ) -> None:
        tf.arcnames.append(arcname)

    @contextmanager
    def accept_synthetic_root(_path: object):
        yield None

    monkeypatch.setattr(archive_module, "anchored_directory", accept_synthetic_root)
    monkeypatch.setattr(archive_module, "add_archive_file_to_tar", record_arcname)

    add_files_to_tar(
        tf,
        PureWindowsPath("C:/workspace"),
        [PureWindowsPath("C:/workspace/src/main.py")],
    )

    assert tf.arcnames == ["src/main.py"]


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "message"),
    [
        ("MAX_ARCHIVE_EXPANDED_BYTES", 4, "maximum size"),
        ("MAX_ARCHIVE_MEMBERS", 1, "more than"),
        ("MAX_ARCHIVE_COMPONENTS", 1, "maximum cumulative component"),
    ],
    ids=["bytes", "members", "path-components"],
)
def test_add_files_to_tar_checks_aggregate_limits_before_next_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit_value: int,
    message: str,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"data")
    second.write_bytes(b"more")
    original_open = os.open
    opened_payloads: list[str] = []

    with archive_module.anchored_directory(tmp_path) as root_descriptor:
        if root_descriptor is None:
            pytest.skip("directory descriptors are not available")

        def record_payload_open(
            path: str | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if path in {first.name, second.name}:
                opened_payloads.append(os.fspath(path))
            return original_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(archive_module.os, "open", record_payload_open)
        monkeypatch.setattr(archive_module, limit_name, limit_value)
        with tarfile.open(fileobj=io.BytesIO(), mode="w:") as tf:
            write_limits = archive_module._ArchiveWriteLimits()
            add_files_to_tar(
                tf,
                tmp_path,
                [first],
                root_descriptor=root_descriptor,
                write_limits=write_limits,
            )
            with pytest.raises(ArchiveError, match=message):
                archive_module.add_packages_to_tar(
                    tf,
                    [second],
                    write_limits=write_limits,
                )
            assert tf.getnames() == [first.name]

    assert opened_payloads == [first.name]


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


def test_extract_archive_rejects_concurrent_target_creation(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "test.tar.gz"
    create_archive(project_dir, archive_path, ArchiveConfig())
    target = tmp_path / "extracted"
    original_rename = archive_module.rename_noreplace
    raced = False

    def create_target(*args, **kwargs) -> None:
        nonlocal raced
        if not raced:
            target.mkdir()
            raced = True
        original_rename(*args, **kwargs)

    monkeypatch.setattr(archive_module, "rename_noreplace", create_target)

    with pytest.raises(ArchiveError, match="target changed"):
        extract_archive(archive_path, target)

    assert raced is True
    assert not any(target.iterdir())


def test_extract_archive_anchors_target_parent_during_publication(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not paths_module.supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    archive_path = tmp_path / "test.tar.gz"
    create_archive(project_dir, archive_path, ArchiveConfig())
    parent = tmp_path / "target-parent"
    target = parent / "extracted"
    displaced = tmp_path / "displaced-target-parent"
    external = tmp_path / "external-target-parent"
    external.mkdir()
    original_rename = archive_module.rename_noreplace
    raced = False

    def replace_parent(*args, **kwargs) -> None:
        nonlocal raced
        if Path(args[1]).name == target.name and not raced:
            parent.rename(displaced)
            parent.symlink_to(external, target_is_directory=True)
            raced = True
        original_rename(*args, **kwargs)

    monkeypatch.setattr(archive_module, "rename_noreplace", replace_parent)

    with pytest.raises(ArchiveError, match="target changed"):
        extract_archive(archive_path, target)

    assert raced is True
    assert (displaced / target.name).is_dir()
    assert not (external / target.name).exists()


def test_extract_archive_does_not_publish_partial_staging(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "test.tar.gz"
    create_archive(project_dir, archive_path, ArchiveConfig())
    target = tmp_path / "extracted"

    original_extract = archive_module.extract_tar_members
    extracted = False

    def fail_extract(tf, members, target) -> None:
        nonlocal extracted
        original_extract(tf, members[:1], target)
        extracted = True
        raise RuntimeError("extraction failed")

    monkeypatch.setattr(archive_module, "extract_tar_members", fail_extract)

    with pytest.raises(RuntimeError, match="extraction failed"):
        extract_archive(archive_path, target)

    assert extracted is True
    assert not target.exists()


def test_extract_archive_validates_source_before_publication(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "test.tar.gz"
    create_archive(project_dir, archive_path, ArchiveConfig())
    target = tmp_path / "extracted"
    original_extract = archive_module.extract_tar_members
    replaced = False

    def replace_source_after_extract(*args, **kwargs) -> None:
        nonlocal replaced
        original_extract(*args, **kwargs)
        replacement = tmp_path / "replacement.tar.gz"
        replacement.write_bytes(archive_path.read_bytes())
        os.replace(replacement, archive_path)
        replaced = True

    monkeypatch.setattr(
        archive_module,
        "extract_tar_members",
        replace_source_after_extract,
    )

    with pytest.raises(
        ArchiveError,
        match="changed while reading|cannot be opened safely",
    ):
        extract_archive(archive_path, target)

    if os.name != "nt":
        assert replaced is True
    assert not target.exists()


def test_extract_archive_rejects_staging_swap_during_member_writes(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not paths_module.supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    archive_path = tmp_path / "test.tar.gz"
    create_archive(project_dir, archive_path, ArchiveConfig())
    target = tmp_path / "extracted"
    displaced = tmp_path / "displaced-staging"
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    original_extract = archive_module.extract_tar_member
    raced = False

    def replace_staging(*args, **kwargs) -> None:
        nonlocal raced
        if not raced:
            root_descriptor = kwargs["root_descriptor"]
            root_identity = os.fstat(root_descriptor)
            candidates = target.parent.glob(f".{target.name}.extract-*/workspace")
            staged = next(
                candidate
                for candidate in candidates
                if (candidate.lstat().st_dev, candidate.lstat().st_ino)
                == (root_identity.st_dev, root_identity.st_ino)
            )
            staged.rename(displaced)
            staged.symlink_to(outside, target_is_directory=True)
            raced = True
        original_extract(*args, **kwargs)

    monkeypatch.setattr(archive_module, "extract_tar_member", replace_staging)

    with pytest.raises(ArchiveError, match="staging changed"):
        extract_archive(archive_path, target)

    assert raced is True
    assert not any(outside.iterdir())
    assert any(displaced.rglob("*"))
    assert not target.exists()


def test_extract_archive_rejects_existing_empty_target(
    project_dir: Path,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "test.tar.gz"
    config = ArchiveConfig()
    create_archive(project_dir, archive_path, config)
    target = tmp_path / "extracted"
    target.mkdir()

    with pytest.raises(ArchiveError, match="existing target"):
        extract_archive(archive_path, target)

    assert not any(target.iterdir())


@pytest.mark.parametrize(
    "target_setup",
    ["empty", "non-empty", "file-target", "symlink-target"],
    ids=["empty", "non-empty", "file-target", "symlink-target"],
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


def test_extract_archive_requires_stdlib_data_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "unsafe-mode.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        member = tarfile.TarInfo("script")
        member.mode = 0o6777
        member.size = 4
        tf.addfile(member, io.BytesIO(b"data"))
    monkeypatch.delattr(tarfile, "data_filter")

    with pytest.raises(ArchiveError, match="requires Python's tar data filter"):
        extract_archive(archive, tmp_path / "target")


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (["path", "path"], "duplicate member"),
        (["Path", "path"], "duplicate member"),
        (
            [
                "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
                "caf\N{LATIN SMALL LETTER E}\N{COMBINING ACUTE ACCENT}",
            ],
            "duplicate member",
        ),
        (["path", "path/child"], "nested under"),
        (["Path", "path/child"], "nested under"),
        (["path/child", "path"], "conflicts with nested member"),
    ],
    ids=[
        "duplicate",
        "case-insensitive-duplicate",
        "unicode-normalized-duplicate",
        "parent-first",
        "case-insensitive-parent-first",
        "child-first",
    ],
)
def test_validate_tar_members_rejects_conflicting_topology(
    names: list[str],
    message: str,
) -> None:
    members = [tarfile.TarInfo(name) for name in names]

    with pytest.raises(ArchiveError, match=message):
        validate_tar_members(members)


def test_validate_tar_members_detects_case_insensitive_link_cycle() -> None:
    first = tarfile.TarInfo("First")
    first.type = tarfile.SYMTYPE
    first.linkname = "second"
    second = tarfile.TarInfo("Second")
    second.type = tarfile.SYMTYPE
    second.linkname = "first"

    with pytest.raises(ArchiveError, match="reference cycle"):
        validate_tar_members([first, second])


@pytest.mark.parametrize(
    ("boundary", "match"),
    [
        pytest.param("path-depth", "maximum path depth", id="path-depth"),
        pytest.param("path-bytes", "maximum length", id="path-bytes"),
        pytest.param("link-depth", "link target", id="link-depth"),
        pytest.param("link-bytes", "link target", id="link-bytes"),
        pytest.param("expanded-bytes", "maximum size", id="expanded-bytes"),
    ],
)
def test_validate_tar_members_enforces_resource_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    match: str,
) -> None:
    member_name = {
        "path-depth": "a/b/c",
        "path-bytes": "large",
    }.get(boundary, "file")
    member = tarfile.TarInfo(member_name)
    if boundary == "path-depth":
        monkeypatch.setattr(archive_module, "MAX_ARCHIVE_PATH_DEPTH", 2)
    elif boundary == "path-bytes":
        monkeypatch.setattr(archive_module, "MAX_ARCHIVE_PATH_BYTES", 4)
    elif boundary == "link-depth":
        monkeypatch.setattr(archive_module, "MAX_ARCHIVE_PATH_DEPTH", 2)
        member.type = tarfile.SYMTYPE
        member.linkname = "a/b/c"
    elif boundary == "link-bytes":
        monkeypatch.setattr(archive_module, "MAX_ARCHIVE_PATH_BYTES", 4)
        member.type = tarfile.SYMTYPE
        member.linkname = "large"
    else:
        monkeypatch.setattr(archive_module, "MAX_ARCHIVE_EXPANDED_BYTES", 3)
        member.size = 4

    with pytest.raises(ArchiveError, match=match):
        validate_tar_members([member])


def test_validate_tar_members_counts_link_fallback_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = tarfile.TarInfo("payload")
    payload.size = 4
    link = tarfile.TarInfo("link")
    link.type = tarfile.SYMTYPE
    link.linkname = "payload"
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_EXPANDED_BYTES", 7)

    with pytest.raises(ArchiveError, match="link fallbacks"):
        validate_tar_members([payload, link])


def test_validate_tar_members_rejects_hardlinks() -> None:
    payload = tarfile.TarInfo("payload")
    payload.size = 4
    link = tarfile.TarInfo("link")
    link.type = tarfile.LNKTYPE
    link.linkname = "payload"

    with pytest.raises(ArchivePathTraversalError):
        validate_tar_members([payload, link])


def test_validate_tar_members_bounds_cumulative_path_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_COMPONENTS", 3)

    with pytest.raises(ArchiveError, match="cumulative component"):
        validate_tar_members(
            [
                tarfile.TarInfo("first/entry"),
                tarfile.TarInfo("second/entry"),
            ]
        )


def test_read_tar_members_stops_at_member_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "too-many.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.addfile(tarfile.TarInfo("first"))
        tf.addfile(tarfile.TarInfo("second"))
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_MEMBERS", 1)

    with open_tar(archive) as tf, pytest.raises(ArchiveError, match="members"):
        read_tar_members(tf)


def test_read_tar_members_checks_size_before_advancing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "too-large.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        first = tarfile.TarInfo("first")
        first.size = 4
        tf.addfile(first, io.BytesIO(b"data"))
        tf.addfile(tarfile.TarInfo("second"))
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_EXPANDED_BYTES", 3)

    with open_tar(archive) as tf:
        original_next = tf.next
        calls = 0

        def recording_next() -> tarfile.TarInfo | None:
            nonlocal calls
            calls += 1
            return original_next()

        monkeypatch.setattr(tf, "next", recording_next)
        with pytest.raises(ArchiveError, match="maximum size"):
            read_tar_members(tf)

    assert calls == 1


def test_open_tar_rejects_symlink_input(project_dir: Path, tmp_path: Path) -> None:
    archive = tmp_path / "archive.tar.gz"
    create_archive(project_dir, archive, ArchiveConfig())
    alias = tmp_path / "alias.tar.gz"
    alias.symlink_to(archive)

    with pytest.raises(ArchiveError, match="opened safely|stable regular file"):
        inspect_archive(alias)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_open_tar_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    archive = tmp_path / "archive.tar.gz"
    os.mkfifo(archive)

    with pytest.raises(ArchiveError, match="stable regular file"):
        inspect_archive(archive)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_file_contains_bytes_rejects_fifo_and_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"needle")
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    assert file_contains_bytes(alias, b"needle") is False
    assert file_contains_bytes(fifo, b"needle") is False


@pytest.mark.parametrize(
    ("metadata", "message", "processor"),
    [
        ("longlink", "link target metadata", "_proc_gnulong"),
        ("pax", "metadata expands", "_proc_pax"),
    ],
    ids=["gnu-longlink", "pax"],
)
def test_open_tar_rejects_oversized_metadata_before_reading_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: str,
    message: str,
    processor: str,
) -> None:
    archive = tmp_path / f"large-{metadata}.tar.gz"
    if metadata == "longlink":
        with tarfile.open(archive, "w:gz", format=tarfile.GNU_FORMAT) as tf:
            member = tarfile.TarInfo("link")
            member.type = tarfile.SYMTYPE
            member.linkname = "x" * 200
            tf.addfile(member)
        monkeypatch.setattr(archive_module, "MAX_ARCHIVE_PATH_BYTES", 64)
    else:
        with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as tf:
            member = tarfile.TarInfo("file")
            member.pax_headers = {"comment": "x" * 200}
            tf.addfile(member)
        monkeypatch.setattr(archive_module, "MAX_ARCHIVE_METADATA_BYTES", 64)

    def fail_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata payload was read")

    monkeypatch.setattr(tarfile.TarInfo, processor, fail_read)

    with pytest.raises(ArchiveError, match=message):
        with open_tar(archive):
            pass


@pytest.mark.parametrize(
    ("limit", "headers", "records", "message"),
    [
        ("MAX_ARCHIVE_METADATA_HEADERS", 2, 1, "metadata headers"),
        ("MAX_ARCHIVE_PAX_RECORDS", 1, 2, "PAX metadata records"),
    ],
    ids=["nested-headers", "pax-records"],
)
def test_open_tar_bounds_pax_metadata_structure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
    headers: int,
    records: int,
    message: str,
) -> None:
    archive = tmp_path / "bounded-pax.tar.gz"
    payload = b"5 x=\n" * records
    padding = b"\0" * (-len(payload) % tarfile.BLOCKSIZE)
    contents = bytearray()
    for index in range(headers):
        metadata = tarfile.TarInfo(f"pax-{index}")
        metadata.type = tarfile.XHDTYPE
        metadata.size = len(payload)
        contents.extend(metadata.tobuf(format=tarfile.PAX_FORMAT))
        contents.extend(payload)
        contents.extend(padding)
    contents.extend(tarfile.TarInfo("file").tobuf(format=tarfile.PAX_FORMAT))
    contents.extend(b"\0" * (tarfile.BLOCKSIZE * 2))
    archive.write_bytes(gzip.compress(contents))
    monkeypatch.setattr(archive_module, limit, 1)

    with pytest.raises(ArchiveError, match=message):
        with open_tar(archive):
            pass


def test_open_tar_bounds_total_interleaved_metadata_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "interleaved-pax.tar.gz"
    payload = b"5 x=\n"
    padding = b"\0" * (-len(payload) % tarfile.BLOCKSIZE)
    contents = bytearray()
    for index in range(2):
        metadata = tarfile.TarInfo(f"pax-{index}")
        metadata.type = tarfile.XHDTYPE
        metadata.size = len(payload)
        contents.extend(metadata.tobuf(format=tarfile.PAX_FORMAT))
        contents.extend(payload)
        contents.extend(padding)
        contents.extend(tarfile.TarInfo(f"file-{index}").tobuf())
    contents.extend(b"\0" * (tarfile.BLOCKSIZE * 2))
    archive.write_bytes(gzip.compress(contents))
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_METADATA_HEADERS_TOTAL", 1)

    with pytest.raises(ArchiveError, match="total metadata headers"):
        with open_tar(archive) as tf:
            read_tar_members(tf)


def test_open_tar_rejects_unsupported_member_before_processing_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "unsupported.tar.gz"
    member = tarfile.TarInfo("device")
    member.type = tarfile.CHRTYPE
    member.size = 4
    archive.write_bytes(
        gzip.compress(
            member.tobuf()
            + b"data"
            + b"\0" * (tarfile.BLOCKSIZE - 4)
            + b"\0" * (tarfile.BLOCKSIZE * 2)
        )
    )

    def fail_processing(*args: object, **kwargs: object) -> None:
        raise AssertionError("unsupported member payload was processed")

    monkeypatch.setattr(tarfile.TarInfo, "_proc_builtin", fail_processing)

    with pytest.raises(ArchiveError, match="unsupported type"):
        with open_tar(archive):
            pass


@pytest.mark.parametrize(
    "pax_headers",
    [
        {"GNU.sparse.size": "1", "GNU.sparse.offset": "0"},
        {"GNU.sparse.map": "0,1"},
        {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"},
    ],
    ids=["pax-0.0", "pax-0.1", "pax-1.0"],
)
def test_open_tar_rejects_pax_sparse_metadata_before_map_processing(
    tmp_path: Path,
    pax_headers: dict[str, str],
) -> None:
    archive = tmp_path / "sparse.tar.gz"
    payload = b"0\n"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        member = tarfile.TarInfo("sparse")
        member.size = len(payload)
        member.pax_headers = pax_headers
        tf.addfile(member, io.BytesIO(payload))

    with pytest.raises(ArchiveError, match="Sparse archive members"):
        with open_tar(archive):
            pass


def test_open_tar_rejects_gnu_sparse_before_extension_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "sparse.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.GNU_FORMAT) as tf:
        member = tarfile.TarInfo("sparse")
        member.type = tarfile.GNUTYPE_SPARSE
        tf.addfile(member)

    def fail_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("GNU sparse extension metadata was read")

    monkeypatch.setattr(tarfile.TarInfo, "_proc_sparse", fail_read)

    with pytest.raises(ArchiveError, match="Sparse archive members"):
        with open_tar(archive):
            pass


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
        ("hardlink", "packages/demo-1.0-h0.conda", "unsupported type"),
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


@pytest.mark.parametrize(
    "explicit_attestation",
    [False, True],
    ids=["default-sidecar", "nested-sidecar"],
)
def test_workspace_archive_signs_exact_archive_receipt(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_attestation: bool,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    attestation = (
        tmp_path / "nested" / "workspace.sigstore.json"
        if explicit_attestation
        else None
    )
    signed_payloads: list[bytes] = []
    bundle_json = '{"bundle": true}'

    def sign(payload: bytes) -> str:
        signed_payloads.append(payload)
        return bundle_json

    monkeypatch.setattr(attestations_module, "sign_attestation_payload", sign)

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=output,
        sign=True,
        attestation=attestation,
    )

    assert len(signed_payloads) == 1
    receipt = ArchiveReceipt.from_payload(signed_payloads[0])
    receipt.verify_archive(archive.path)
    assert receipt.workspace_paths == ("conda.toml", "conda.lock")
    assert archive.receipt_path is None
    assert archive.attestation_path == (
        attestation.absolute()
        if attestation is not None
        else WorkspaceArchive.default_attestation_path(archive.path)
    )
    assert archive.attestation_path.read_text(encoding="utf-8") == f"{bundle_json}\n"
    with open_tar(archive.path) as tar:
        assert archive.attestation_path.name not in tar.getnames()


def test_workspace_archive_sign_and_receipt_use_same_statement(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_payloads: list[bytes] = []
    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        lambda payload: signed_payloads.append(payload) or '{"bundle": true}',
    )

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
        sign=True,
    )

    assert archive.receipt_path is not None
    assert ArchiveReceipt.load(archive.receipt_path).statement == (
        ArchiveReceipt.from_payload(signed_payloads[0]).statement
    )


def test_workspace_archive_sign_dry_run_does_not_request_oidc(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "workspace.tar.gz"

    def fail_sign(_payload: bytes) -> str:
        raise AssertionError("dry-run requested OIDC")

    monkeypatch.setattr(attestations_module, "sign_attestation_payload", fail_sign)

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=output,
        sign=True,
        dry_run=True,
    )

    assert archive.path == output.resolve()
    assert not output.exists()
    assert archive.attestation_path is not None
    assert not archive.attestation_path.exists()


def test_workspace_archive_build_receipt_uses_legacy_signature(
    workspace_archive_project: Path,
) -> None:
    output = workspace_archive_project / "workspace.tar.gz"
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=output,
    )
    _, config = detect_and_parse(workspace_archive_project / "conda.toml")

    receipt = WorkspaceArchive.build_receipt(
        ctx=WorkspaceContext(config),
        archive_path=archive.path,
        archive_config=config.archive,
        manifest_path=workspace_archive_project / "conda.toml",
        lockfile_path=workspace_archive_project / "conda.lock",
        options={"bundle": False, "lock": False},
    )

    receipt.verify_archive(archive.path)
    assert receipt.workspace_paths == ("conda.toml", "conda.lock")


@pytest.mark.parametrize("dry_run", [False, True], ids=["create", "dry-run"])
@pytest.mark.parametrize(
    "source",
    [
        "manifest",
        "manifest-relative-token",
        "manifest-encoded-relative-token",
        "manifest-double-encoded-relative-token",
        "manifest-task-relative-token",
        "lockfile",
        "lockfile-metadata",
        "lockfile-comment",
        "lockfile-quoted-metadata",
    ],
)
def test_workspace_archive_create_rejects_embedded_credentials(
    workspace_archive_project: Path,
    tmp_path: Path,
    dry_run: bool,
    source: str,
) -> None:
    if source.startswith("manifest"):
        path = workspace_archive_project / "conda.toml"
        if source == "manifest-task-relative-token":
            path.write_text(
                path.read_text(encoding="utf-8")
                + '\n[tasks]\nleak = "curl -fsSL t/VALIDATION-LEAK/private/file.txt"\n',
                encoding="utf-8",
            )
        else:
            channel = {
                "manifest": "https://user:secret@repo.example/conda",
                "manifest-relative-token": "nested/t/secret/conda-forge",
                "manifest-encoded-relative-token": "t%2Fsecret%2Fconda-forge",
                "manifest-double-encoded-relative-token": (
                    "t%252Fsecret%252Fconda-forge"
                ),
            }[source]
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    'channels = ["conda-forge"]',
                    f"channels = [{json.dumps(channel)}]",
                ),
                encoding="utf-8",
            )
    elif source == "lockfile":
        path = workspace_archive_project / "conda.lock"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "https://conda.anaconda.org/conda-forge/",
                "https://user:secret@conda.anaconda.org/conda-forge/",
            ),
            encoding="utf-8",
        )
    elif source == "lockfile-metadata":
        path = workspace_archive_project / "conda.lock"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "metadata:\n"
            + "  mirror: https://user:secret@repo.example/conda\n",
            encoding="utf-8",
        )
    else:
        path = workspace_archive_project / "conda.lock"
        credential = "https://user:secret@repo.example/t/token/conda?query=secret"
        addition = (
            f"# source {credential}\n"
            if source == "lockfile-comment"
            else f"metadata: {{note: {json.dumps(credential)}}}\n"
        )
        path.write_text(
            path.read_text(encoding="utf-8") + addition,
            encoding="utf-8",
        )
    output = tmp_path / "workspace.tar.gz"

    with pytest.raises(ArchiveError, match="credentials embedded"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            dry_run=dry_run,
        )

    assert not output.exists()


def test_workspace_archive_create_binds_manifest_bytes_to_archive(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = workspace_archive_project / "conda.toml"
    output = tmp_path / "workspace.tar.gz"
    original_add = archive_module.add_regular_file_to_tar
    replaced = False

    def replace_manifest_before_add(*args, **kwargs) -> None:
        nonlocal replaced
        path = args[1]
        if path == manifest and not replaced:
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "archive-api-test",
                    "archive-api-evil",
                ),
                encoding="utf-8",
            )
            replaced = True
        original_add(*args, **kwargs)

    monkeypatch.setattr(
        archive_module,
        "add_regular_file_to_tar",
        replace_manifest_before_add,
    )

    with pytest.raises(ArchiveHashMismatchError, match="conda.toml"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
        )

    assert replaced is True
    assert not output.exists()


def test_workspace_archive_create_binds_ordinary_file_generation(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = workspace_archive_project / "src" / "app.py"
    output = tmp_path / "workspace.tar.gz"
    original_addfile = tarfile.TarFile.addfile
    rewritten = False

    def rewrite_after_add(
        self: tarfile.TarFile,
        member: tarfile.TarInfo,
        fileobj: object = None,
    ) -> None:
        nonlocal rewritten
        original_addfile(self, member, fileobj)  # ty: ignore[invalid-argument-type]
        if member.name == "src/app.py" and not rewritten:
            source.write_text("print('changed')\n", encoding="utf-8")
            rewritten = True

    monkeypatch.setattr(tarfile.TarFile, "addfile", rewrite_after_add)

    with pytest.raises(ArchiveError, match="changed while reading"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
        )

    assert rewritten is True
    assert not output.exists()


def test_workspace_archive_create_does_not_follow_replaced_ordinary_file(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = workspace_archive_project / "src" / "app.py"
    output = tmp_path / "workspace.tar.gz"
    original_add = archive_module.add_regular_file_to_tar
    replaced = False

    def replace_before_open(*args, **kwargs) -> None:
        nonlocal replaced
        path = args[1]
        if path == source and not replaced:
            source.unlink()
            source.symlink_to(workspace_archive_project / "conda.toml")
            replaced = True
        original_add(*args, **kwargs)

    monkeypatch.setattr(
        archive_module,
        "add_regular_file_to_tar",
        replace_before_open,
    )

    with pytest.raises(ArchiveError, match="Cannot archive regular file safely"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
        )

    assert replaced is True
    assert not output.exists()


def test_create_archive_reads_member_through_anchored_parent(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not paths_module.supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    source_parent = project_dir / "src"
    moved_parent = project_dir / "src-original"
    output = tmp_path / "workspace.tar.gz"
    original_open = os.open
    raced = False

    def replace_parent_before_leaf_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal raced
        if path == "main.py" and dir_fd is not None and not raced:
            raced = True
            source_parent.rename(moved_parent)
            source_parent.mkdir()
            (source_parent / "main.py").write_text(
                "print('replaced')\n",
                encoding="utf-8",
            )
            assert flags & getattr(os, "O_NOFOLLOW", 0)
            assert flags & getattr(os, "O_NONBLOCK", 0)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(archive_module.os, "open", replace_parent_before_leaf_open)

    create_archive(project_dir, output, ArchiveConfig())

    assert raced is True
    with open_tar(output) as tf:
        stream = tf.extractfile("src/main.py")
        assert stream is not None
        assert stream.read() == b"print('hello')\n"


def test_workspace_archive_create_uses_frozen_anchored_workspace(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not paths_module.supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    output = tmp_path / "workspace.tar.gz"
    displaced = tmp_path / "trusted-workspace"
    replacement = tmp_path / "replacement-workspace"
    shutil.copytree(workspace_archive_project, replacement)
    (replacement / "src" / "app.py").write_text(
        "print('attacker')\n",
        encoding="utf-8",
    )
    (replacement / "untracked-secret.txt").write_text(
        "attacker selected this path\n",
        encoding="utf-8",
    )
    original_create = archive_module.create_archive
    swapped = False

    def swap_root_after_collection(*args, **kwargs) -> Path:
        nonlocal swapped
        workspace_archive_project.rename(displaced)
        replacement.rename(workspace_archive_project)
        swapped = True
        try:
            return original_create(*args, **kwargs)
        finally:
            workspace_archive_project.rename(replacement)
            displaced.rename(workspace_archive_project)

    monkeypatch.setattr(
        archive_module,
        "create_archive",
        swap_root_after_collection,
    )

    WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=output,
    )

    assert swapped is True
    with open_tar(output) as tf:
        assert "untracked-secret.txt" not in tf.getnames()
        stream = tf.extractfile("src/app.py")
        assert stream is not None
        assert stream.read() == b"print('hello')\n"


def test_workspace_archive_create_rejects_root_swap_during_collection(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not paths_module.supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    output = tmp_path / "workspace.tar.gz"
    displaced = tmp_path / "displaced-workspace"
    replacement = tmp_path / "replacement-workspace"
    shutil.copytree(workspace_archive_project, replacement)
    original_collect = archive_module.collect_archive_files
    swapped = False

    def swap_and_restore_root(*args, **kwargs) -> list[Path]:
        nonlocal swapped
        files = original_collect(*args, **kwargs)
        workspace_archive_project.rename(displaced)
        replacement.rename(workspace_archive_project)
        workspace_archive_project.rename(replacement)
        displaced.rename(workspace_archive_project)
        swapped = True
        return files

    monkeypatch.setattr(
        archive_module,
        "collect_archive_files",
        swap_and_restore_root,
    )

    with pytest.raises(ArchiveError, match="root changed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
        )

    assert swapped is True
    assert not output.exists()


def test_create_archive_preserves_existing_output_on_write_failure(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    output.write_bytes(b"existing archive")

    def fail_add(*args: object, **kwargs: object) -> None:
        raise RuntimeError("archive write failed")

    monkeypatch.setattr(archive_module, "add_files_to_tar", fail_add)

    with pytest.raises(RuntimeError, match="archive write failed"):
        create_archive(project_dir, output, ArchiveConfig())

    assert output.read_bytes() == b"existing archive"


@pytest.mark.parametrize(
    ("mutation", "preserve_generation"),
    [
        ("replace", False),
        ("rewrite", False),
        ("rewrite", True),
    ],
    ids=["replace", "rewrite", "same-generation-rewrite"],
)
def test_create_archive_rejects_changed_validated_output(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    preserve_generation: bool,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    original_content = b"existing archive"
    concurrent_content = b"changed archive!"
    output.write_bytes(original_content)
    original_atomic_binary_writer = archive_module.atomic_binary_writer

    @contextmanager
    def mutate_before_write(path: Path, **kwargs: Any):
        if mutation == "replace":
            replacement = output.with_name("replacement.tar.gz")
            replacement.write_bytes(concurrent_content)
            replacement.replace(output)
        else:
            output.write_bytes(concurrent_content)
        if preserve_generation:
            kwargs["expected_generation"] = paths_module.regular_file_generation(output)
        with original_atomic_binary_writer(path, **kwargs) as stream:
            yield stream

    monkeypatch.setattr(
        archive_module,
        "atomic_binary_writer",
        mutate_before_write,
    )

    with pytest.raises(ValueError, match="changed before writing"):
        create_archive(project_dir, output, ArchiveConfig())

    assert len(concurrent_content) == len(original_content)
    assert output.read_bytes() == concurrent_content


def test_workspace_archive_rejects_symlink_alias(
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

    archive = WorkspaceArchive(alias, receipt=created.receipt_path)

    assert archive.path == alias.absolute()
    assert archive.receipt_path == created.receipt_path
    with pytest.raises(ArchiveError, match="opened safely|stable regular file"):
        archive.inspect()


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
        ("attestation-hardlink", "archived workspace input"),
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
    attestation = None
    if unsafe_alias == "manifest-symlink":
        manifest = workspace_archive_project / "conda.toml"
        target = tmp_path / "outside-manifest"
        manifest.rename(target)
        manifest.symlink_to(target)
    elif unsafe_alias == "archive-symlink":
        output.symlink_to(source)
    elif unsafe_alias == "receipt-hardlink":
        receipt = workspace_archive_project / "receipt.json"
        receipt.hardlink_to(source)
    else:
        attestation = workspace_archive_project / "archive.sigstore.json"
        attestation.hardlink_to(source)
    before = snapshot_tree(tmp_path)

    with pytest.raises(ArchiveError, match=message):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=receipt,
            sign=attestation is not None,
            attestation=attestation,
            dry_run=True,
        )

    assert snapshot_tree(tmp_path) == before
    assert source.read_text(encoding="utf-8") == "print('hello')\n"


def test_workspace_archive_create_rejects_symlinked_output_parent(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(external, target_is_directory=True)

    with pytest.raises(NotADirectoryError, match="symbolic link"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=alias / "workspace.tar.gz",
        )

    assert not (external / "workspace.tar.gz").exists()


def test_workspace_archive_create_rejects_manifest_symlink_before_reading(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = workspace_archive_project / "conda.toml"
    target = tmp_path / "outside-manifest"
    manifest.rename(target)
    manifest.symlink_to(target)
    original_read_text = Path.read_text
    reads: list[Path] = []

    def recording_read_text(path: Path, *args: object, **kwargs: object) -> str:
        reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", recording_read_text)

    with pytest.raises(ArchiveError, match="symbolic links are not supported"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=tmp_path / "workspace.tar.gz",
            dry_run=True,
        )

    assert manifest not in reads


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


def test_workspace_archive_create_reads_current_manifest(
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


def test_workspace_archive_create_reads_current_manifest_selection(
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


def test_workspace_archive_lock_rejects_concurrent_manifest_change(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = workspace_archive_project / "conda.toml"
    lockfile = workspace_archive_project / "conda.lock"
    original_lock = lockfile.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "conda_workspaces.resolver.resolve_all_environments",
        lambda config, platform: {"default": object()},
    )

    def render_and_change_manifest(*args: object, **kwargs: object) -> str:
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "\n# concurrent change\n",
            encoding="utf-8",
        )
        return "version: 1\nenvironments: {}\npackages: []\n"

    monkeypatch.setattr(
        "conda_workspaces.lockfile.render_lockfile",
        render_and_change_manifest,
    )
    output = tmp_path / "workspace.tar.gz"

    with pytest.raises(CondaWorkspacesError, match="manifest changed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            lock=True,
        )

    assert lockfile.read_text(encoding="utf-8") == original_lock
    assert not output.exists()


@pytest.mark.parametrize("target", ["archive", "receipt", "lockfile"])
@pytest.mark.parametrize("mutation", ["rewrite", "replace"])
def test_workspace_archive_lock_captures_outputs_before_rendering(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    mutation: str,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt = output.with_name(f"{output.name}.receipt.json")
    lockfile = workspace_archive_project / "conda.lock"
    selected = {
        "archive": output,
        "receipt": receipt,
        "lockfile": lockfile,
    }[target]
    selected.write_bytes(f"original {target}".encode())
    concurrent = f"concurrent {target}".encode()
    generated_lock = (
        f"version: 1\nenvironments:\n  default:\n    channels: []\n"
        f"    packages:\n      {conda_context.subdir}: []\npackages: []\n"
    )
    monkeypatch.setattr(
        "conda_workspaces.resolver.resolve_all_environments",
        lambda config, platform: {"default": object()},
    )

    def render_and_replace_output(*args: object, **kwargs: object) -> str:
        if mutation == "rewrite":
            selected.write_bytes(concurrent)
        else:
            replacement = selected.with_name(f"{selected.name}.replacement")
            replacement.write_bytes(concurrent)
            replacement.replace(selected)
        return generated_lock

    monkeypatch.setattr(
        "conda_workspaces.lockfile.render_lockfile",
        render_and_replace_output,
    )

    with pytest.raises((CondaWorkspacesError, ValueError), match="changed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            lock=True,
            receipt=target == "receipt",
        )

    assert selected.read_bytes() == concurrent


def test_workspace_archive_reads_bundle_lock_under_publication_guard(
    lockfile_with_packages: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = False

    @contextmanager
    def record_guard(stream):
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    original_collect = archive_module.collect_bundle_packages

    def collect_under_guard(*args: object, **kwargs: object) -> list[Path]:
        assert held
        return original_collect(*args, **kwargs)

    monkeypatch.setattr("conda_workspaces.publication.lock", record_guard)
    monkeypatch.setattr(
        archive_module,
        "collect_bundle_packages",
        collect_under_guard,
    )
    cache_dir = lockfile_with_packages / "pkg_cache"

    with conda_context._override("_pkgs_dirs", (str(cache_dir),)):
        archive = WorkspaceArchive.create(
            workspace=lockfile_with_packages,
            output=tmp_path / "workspace.tar.gz",
            bundle=True,
        )

    assert archive.path.is_file()


def test_workspace_archive_reads_receipt_lock_under_publication_guard(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = False

    @contextmanager
    def record_guard(stream):
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    original_load = archive_module.load_lockfile_data

    def load_under_guard(content: str | bytes) -> dict:
        assert held
        return original_load(content)

    monkeypatch.setattr("conda_workspaces.publication.lock", record_guard)
    monkeypatch.setattr(archive_module, "load_lockfile_data", load_under_guard)

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
    )

    assert archive.receipt_path is not None
    assert archive.receipt_path.is_file()


def test_workspace_archive_receipt_uses_captured_manifest_and_lockfile(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = workspace_archive_project / "conda.toml"
    lockfile = workspace_archive_project / "conda.lock"
    output = tmp_path / "workspace.tar.gz"
    original_build = ArchiveReceipt.build_from_captured
    replaced = False

    def replace_inputs_before_receipt(**kwargs) -> ArchiveReceipt:
        nonlocal replaced
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "archive-api-test",
                "archive-api-replaced",
            ),
            encoding="utf-8",
        )
        lockfile.write_text(
            lockfile.read_text(encoding="utf-8") + "# replaced\n",
            encoding="utf-8",
        )
        replaced = True
        return original_build(**kwargs)

    monkeypatch.setattr(
        ArchiveReceipt,
        "build_from_captured",
        staticmethod(replace_inputs_before_receipt),
    )

    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=output,
        receipt=True,
    )

    assert replaced is True
    assert archive.receipt_path is not None
    receipt = ArchiveReceipt.load(archive.receipt_path)
    with open_tar(output) as tf:
        manifest_stream = tf.extractfile("conda.toml")
        lockfile_stream = tf.extractfile("conda.lock")
        assert manifest_stream is not None
        assert lockfile_stream is not None
        archived_manifest = manifest_stream.read()
        archived_lockfile = lockfile_stream.read()
    subjects = receipt.subject_digests
    assert subjects[output.name] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert subjects["conda.toml"] == hashlib.sha256(archived_manifest).hexdigest()
    assert subjects["conda.lock"] == hashlib.sha256(archived_lockfile).hexdigest()
    assert subjects["conda.toml"] != hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert subjects["conda.lock"] != hashlib.sha256(lockfile.read_bytes()).hexdigest()


@pytest.mark.parametrize("receipt", [False, True], ids=["archive", "receipt"])
def test_workspace_archive_rejects_post_publication_replacement(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt: bool,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt_path = ArchiveReceipt.default_path(output)
    original_create = archive_module.create_archive
    replaced = False

    def replace_after_create(*args, **kwargs) -> Path:
        nonlocal replaced
        result = original_create(*args, **kwargs)
        replacement = tmp_path / "replacement.tar.gz"
        replacement.write_bytes(b"replacement archive")
        os.replace(replacement, output)
        replaced = True
        return result

    monkeypatch.setattr(archive_module, "create_archive", replace_after_create)

    with pytest.raises(ArchiveError, match="Archive output changed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=receipt,
        )

    assert replaced is True
    assert output.read_bytes() == b"replacement archive"
    assert not receipt_path.exists()


@pytest.mark.parametrize(
    ("race", "expected_output"),
    [
        ("rewrite", b"rewritten archive"),
        ("replace", b"replacement archive"),
    ],
    ids=["rewrite", "replace"],
)
def test_workspace_archive_receipt_rejects_changed_archive_output(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
    expected_output: bytes,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt_path = ArchiveReceipt.default_path(output)
    original_write = ArchiveReceipt.write

    def change_archive_after_write(
        self: ArchiveReceipt,
        path: Path,
        **kwargs: object,
    ) -> Path:
        result = original_write(self, path, **kwargs)
        if race == "rewrite":
            output.write_bytes(b"rewritten archive")
        else:
            replacement = tmp_path / "replacement.tar.gz"
            replacement.write_bytes(b"replacement archive")
            os.replace(replacement, output)
        return result

    monkeypatch.setattr(ArchiveReceipt, "write", change_archive_after_write)

    with pytest.raises(ArchiveError, match="Archive output changed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
        )

    assert output.read_bytes() == expected_output
    assert not receipt_path.exists()


@pytest.mark.parametrize(
    ("race", "expected_output"),
    [
        ("rewrite", b"rewritten archive"),
        ("replace", b"replacement archive"),
    ],
    ids=["rewrite", "replace"],
)
def test_workspace_archive_sign_rejects_changed_archive_output(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
    expected_output: bytes,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    attestation_path = WorkspaceArchive.default_attestation_path(output)

    def change_archive_during_sign(_payload: bytes) -> str:
        if race == "rewrite":
            output.write_bytes(b"rewritten archive")
        else:
            replacement = tmp_path / "replacement.tar.gz"
            replacement.write_bytes(b"replacement archive")
            os.replace(replacement, output)
        return '{"bundle": true}'

    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        change_archive_during_sign,
    )

    with pytest.raises(ArchiveError, match="Archive output changed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            sign=True,
        )

    assert output.read_bytes() == expected_output
    assert not attestation_path.exists()


def test_workspace_archive_sign_failure_preserves_existing_outputs(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt_path = ArchiveReceipt.default_path(output)
    attestation_path = WorkspaceArchive.default_attestation_path(output)
    existing = {
        output: b"existing archive\n",
        receipt_path: b"existing receipt\n",
        attestation_path: b"existing attestation\n",
    }
    for path, content in existing.items():
        path.write_bytes(content)

    def fail_sign(_payload: bytes) -> str:
        raise AttestationError("signing failed")

    monkeypatch.setattr(attestations_module, "sign_attestation_payload", fail_sign)

    with pytest.raises(AttestationError, match="signing failed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
            sign=True,
        )

    assert {path: path.read_bytes() for path in existing} == existing


@pytest.mark.parametrize(
    "swapped_output",
    ["archive", "receipt"],
    ids=["archive-parent", "receipt-parent"],
)
def test_workspace_archive_sign_rejects_output_parent_swap(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swapped_output: str,
) -> None:
    archive_parent = tmp_path / "archive-output"
    receipt_parent = tmp_path / "receipt-output"
    attestation_parent = tmp_path / "attestation-output"
    for parent in (archive_parent, receipt_parent, attestation_parent):
        parent.mkdir()
    output = archive_parent / "workspace.tar.gz"
    receipt_path = receipt_parent / "workspace.receipt.json"
    attestation_path = attestation_parent / "workspace.sigstore.json"
    selected_parent = archive_parent if swapped_output == "archive" else receipt_parent
    displaced_parent = tmp_path / f"displaced-{swapped_output}"
    marker = b"concurrent parent\n"

    def swap_parent(_payload: bytes) -> str:
        selected_parent.rename(displaced_parent)
        selected_parent.mkdir()
        (selected_parent / "marker").write_bytes(marker)
        return '{"bundle": true}'

    monkeypatch.setattr(attestations_module, "sign_attestation_payload", swap_parent)

    with pytest.raises(ArchiveError, match="changed before publication"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=receipt_path,
            sign=True,
            attestation=attestation_path,
        )

    assert {path.name for path in selected_parent.iterdir()} == {"marker"}
    assert (selected_parent / "marker").read_bytes() == marker
    assert not attestation_path.exists()
    assert not output.exists()
    assert not receipt_path.exists()


@pytest.mark.parametrize(
    "replaced_output",
    ["archive", "receipt", "attestation"],
    ids=["archive", "receipt", "attestation"],
)
def test_workspace_archive_sign_rejects_final_output_replacement(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replaced_output: str,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt_path = ArchiveReceipt.default_path(output)
    attestation_path = WorkspaceArchive.default_attestation_path(output)
    existing = {
        output: b"existing archive\n",
        receipt_path: b"existing receipt\n",
        attestation_path: b"existing attestation\n",
    }
    for path, content in existing.items():
        path.write_bytes(content)
    replacement_content = f"concurrent {replaced_output}\n".encode()

    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        lambda payload: '{"bundle": true}',
    )
    if replaced_output in {"archive", "receipt"}:
        original_write = attestations_module.AttestationOutput.write
        replaced_path = output if replaced_output == "archive" else receipt_path

        def replace_after_attestation_write(
            self: attestations_module.AttestationOutput,
            bundle_json: str,
        ) -> Path:
            result = original_write(self, bundle_json)
            replacement = tmp_path / f"replacement-{replaced_output}"
            replacement.write_bytes(replacement_content)
            replacement.replace(replaced_path)
            return result

        monkeypatch.setattr(
            attestations_module.AttestationOutput,
            "write",
            replace_after_attestation_write,
        )
    else:
        original_capture = WorkspaceArchive.capture_published_output

        def replace_after_attestation_capture(path: Path, **kwargs):
            result = original_capture(path, **kwargs)
            if kwargs["label"] == "Attestation output":
                replacement = tmp_path / "replacement-attestation.json"
                replacement.write_bytes(replacement_content)
                replacement.replace(attestation_path)
            return result

        monkeypatch.setattr(
            WorkspaceArchive,
            "capture_published_output",
            staticmethod(replace_after_attestation_capture),
        )

    with pytest.raises(ArchiveError, match=f"{replaced_output.title()} output changed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
            sign=True,
        )

    replaced_path = {
        "archive": output,
        "receipt": receipt_path,
        "attestation": attestation_path,
    }[replaced_output]
    assert replaced_path.read_bytes() == replacement_content
    for path, content in existing.items():
        if path != replaced_path:
            assert path.read_bytes() == content


@pytest.mark.parametrize(
    "rewritten_output",
    ["archive", "receipt"],
    ids=["archive", "receipt"],
)
def test_workspace_archive_sign_binds_existing_output_digests(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rewritten_output: str,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt_path = ArchiveReceipt.default_path(output)
    existing = {
        output: b"existing archive",
        receipt_path: b"existing receipt",
    }
    concurrent = {
        output: b"changed archive!",
        receipt_path: b"changed receipt!",
    }
    for path, content in existing.items():
        path.write_bytes(content)
        assert len(concurrent[path]) == len(content)
    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        lambda payload: '{"bundle": true}',
    )

    target = output if rewritten_output == "archive" else receipt_path
    if rewritten_output == "archive":
        original_writer = archive_module.atomic_binary_writer

        @contextmanager
        def rewrite_archive_before_publication(path: Path, **kwargs):
            if path == target:
                target.write_bytes(concurrent[target])
                kwargs["expected_generation"] = paths_module.regular_file_generation(
                    target
                )
            with original_writer(path, **kwargs) as stream:
                yield stream

        monkeypatch.setattr(
            archive_module,
            "atomic_binary_writer",
            rewrite_archive_before_publication,
        )
    else:
        original_receipt_write = ArchiveReceipt.write

        def rewrite_receipt_before_publication(
            self: ArchiveReceipt,
            path: Path,
            **kwargs: Any,
        ) -> Path:
            target.write_bytes(concurrent[target])
            kwargs["expected_generation"] = paths_module.regular_file_generation(target)
            return original_receipt_write(self, path, **kwargs)

        monkeypatch.setattr(
            ArchiveReceipt,
            "write",
            rewrite_receipt_before_publication,
        )

    with pytest.raises(ArchiveError, match="changed before publication"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
            sign=True,
        )

    assert target.read_bytes() == concurrent[target]
    for path, content in existing.items():
        if path != target:
            assert path.read_bytes() == content
    assert not WorkspaceArchive.default_attestation_path(output).exists()


def test_require_published_output_rechecks_digest_when_generation_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "archive.tar.gz"
    original = b"AAAA"
    path.write_bytes(original)
    generation = paths_module.regular_file_generation(path)
    assert generation is not None
    parent_identity = paths_module.capture_directory_identity(path.parent)
    initial = path.stat()
    path.write_bytes(b"BBBB")
    os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
    monkeypatch.setattr(
        archive_module,
        "regular_file_generation",
        lambda candidate: generation,
    )

    with pytest.raises(ArchiveError, match="Archive output changed") as exc_info:
        WorkspaceArchive.require_published_output(
            path,
            expected_generation=generation,
            expected_sha256=hashlib.sha256(original).hexdigest(),
            maximum_bytes=len(original),
            expected_parent_identity=parent_identity,
            label="Archive output",
        )
    assert isinstance(exc_info.value.__cause__, ArchiveHashMismatchError)


@pytest.mark.parametrize(
    "previous_content",
    [None, b"previous archive"],
    ids=["remove", "restore"],
)
@pytest.mark.parametrize(
    "digest_matches",
    [True, False],
    ids=["matching-digest", "mismatched-digest"],
)
def test_restore_published_output_requires_matching_digest(
    tmp_path: Path,
    previous_content: bytes | None,
    digest_matches: bool,
) -> None:
    path = tmp_path / "archive.tar.gz"
    content = b"published archive"
    path.write_bytes(content)
    generation = paths_module.regular_file_generation(path)
    assert generation is not None
    parent_identity = paths_module.capture_directory_identity(path.parent)
    backup = None
    if previous_content is not None:
        backup = tmp_path / "previous.tar.gz"
        backup.write_bytes(previous_content)
    published_content = content if digest_matches else b"different archive"

    restored = WorkspaceArchive.restore_published_output(
        path,
        published_generation=generation,
        published_sha256=hashlib.sha256(published_content).hexdigest(),
        backup=backup,
        maximum_bytes=1024,
        label="Archive output",
        expected_parent_identity=parent_identity,
    )

    assert restored is digest_matches
    if not digest_matches:
        assert path.read_bytes() == content
    elif previous_content is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == previous_content


@pytest.mark.parametrize("replace", [False, True], ids=["matching", "replaced"])
def test_remove_created_output_preserves_legacy_signature(
    tmp_path: Path,
    replace: bool,
) -> None:
    path = tmp_path / "archive.tar.gz"
    path.write_bytes(b"created archive")
    current = path.stat()
    expected = current.st_dev, current.st_ino
    if replace:
        replacement = tmp_path / "replacement.tar.gz"
        replacement.write_bytes(b"replacement archive")
        replacement.replace(path)

    with pytest.deprecated_call(match="does not bind file content"):
        WorkspaceArchive.remove_created_output(path, expected)

    assert path.exists() is replace


@pytest.mark.parametrize(
    "previous_content",
    [None, b"previous archive"],
    ids=["remove-new", "restore-existing"],
)
def test_restore_published_output_reports_recovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_content: bytes | None,
) -> None:
    path = tmp_path / "archive.tar.gz"
    recovery = tmp_path / ".archive.tar.gz.recovery.rollback"
    claimant = b"claimant archive"
    content = b"published archive"
    path.write_bytes(content)
    generation = paths_module.regular_file_generation(path)
    assert generation is not None
    parent_identity = paths_module.capture_directory_identity(path.parent)
    backup = None
    if previous_content is not None:
        backup = tmp_path / "previous.tar.gz"
        backup.write_bytes(previous_content)

    def fail_removal(*args: Any, **kwargs: Any) -> bool:
        path.rename(recovery)
        path.write_bytes(claimant)
        raise FileRecoveryError(path, recovery, "Removal failed.")

    @contextmanager
    def fail_restore(*args: Any, **kwargs: Any) -> Iterator[io.BytesIO]:
        yield io.BytesIO()
        path.rename(recovery)
        path.write_bytes(claimant)
        raise FileRecoveryError(path, recovery, "Restore failed.")

    if backup is None:
        monkeypatch.setattr(archive_module, "remove_file_generation", fail_removal)
    else:
        monkeypatch.setattr(archive_module, "atomic_binary_writer", fail_restore)

    with pytest.raises(FileRecoveryError) as exc_info:
        WorkspaceArchive.restore_published_output(
            path,
            published_generation=generation,
            published_sha256=hashlib.sha256(content).hexdigest(),
            backup=backup,
            maximum_bytes=1024,
            label="Archive output",
            expected_parent_identity=parent_identity,
        )

    assert path.read_bytes() == claimant
    assert recovery.read_bytes() == content
    assert exc_info.value.recovery_path == recovery
    if backup is not None:
        assert backup.read_bytes() == previous_content


@pytest.mark.parametrize(
    "previous_attestation",
    [None, b"existing attestation\n"],
    ids=["missing", "existing"],
)
def test_workspace_archive_sign_restores_attestation_after_post_publish_failure(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_attestation: bytes | None,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt_path = ArchiveReceipt.default_path(output)
    attestation_path = WorkspaceArchive.default_attestation_path(output)
    existing = {
        output: b"existing archive\n",
        receipt_path: b"existing receipt\n",
    }
    for path, content in existing.items():
        path.write_bytes(content)
    if previous_attestation is not None:
        attestation_path.write_bytes(previous_attestation)
    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        lambda payload: '{"bundle": true}',
    )
    original_publish = attestations_module.AttestationOutput._publish_content
    failed = False

    def publish_then_fail(
        self: attestations_module.AttestationOutput,
        content: bytes,
        *,
        expected_generation: Any,
        expected_sha256: str | None,
    ) -> tuple[bytes, paths_module.FileGeneration]:
        nonlocal failed
        result = original_publish(
            self,
            content,
            expected_generation=expected_generation,
            expected_sha256=expected_sha256,
        )
        if not failed:
            failed = True
            raise ValueError("post-publication validation failed")
        return result

    monkeypatch.setattr(
        attestations_module.AttestationOutput,
        "_publish_content",
        publish_then_fail,
    )

    with pytest.raises(AttestationError, match="changed before publication"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
            sign=True,
        )

    assert failed is True
    assert {path: path.read_bytes() for path in existing} == existing
    if previous_attestation is None:
        assert not attestation_path.exists()
    else:
        assert attestation_path.read_bytes() == previous_attestation


@pytest.mark.parametrize(
    "previous_archive",
    [None, b"existing archive\n"],
    ids=["missing", "existing"],
)
def test_workspace_archive_sign_restores_archive_after_post_publish_failure(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_archive: bytes | None,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    if previous_archive is not None:
        output.write_bytes(previous_archive)
    original_writer = archive_module.atomic_binary_writer

    @contextmanager
    def fail_after_archive_publication(path: Path, **kwargs):
        with original_writer(path, **kwargs) as stream:
            yield stream
        if path == output:
            raise OSError("post-publication validation failed")

    monkeypatch.setattr(
        archive_module,
        "atomic_binary_writer",
        fail_after_archive_publication,
    )
    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        lambda payload: '{"bundle": true}',
    )

    with pytest.raises(ArchiveError, match="cannot be published safely"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
            sign=True,
        )

    if previous_archive is None:
        assert not output.exists()
    else:
        assert output.read_bytes() == previous_archive
    assert not ArchiveReceipt.default_path(output).exists()
    assert not WorkspaceArchive.default_attestation_path(output).exists()


@pytest.mark.parametrize(
    "previous_receipt",
    [None, b"existing receipt\n"],
    ids=["missing", "existing"],
)
def test_workspace_archive_sign_restores_receipt_after_post_publish_failure(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_receipt: bytes | None,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    output.write_bytes(b"existing archive\n")
    receipt_path = ArchiveReceipt.default_path(output)
    if previous_receipt is not None:
        receipt_path.write_bytes(previous_receipt)
    original_write = ArchiveReceipt.write
    failed = False

    def fail_after_receipt_publication(
        self: ArchiveReceipt,
        path: Path,
        **kwargs,
    ) -> Path:
        nonlocal failed
        result = original_write(self, path, **kwargs)
        if not failed:
            failed = True
            raise OSError("post-publication validation failed")
        return result

    monkeypatch.setattr(ArchiveReceipt, "write", fail_after_receipt_publication)
    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        lambda payload: '{"bundle": true}',
    )

    with pytest.raises(ArchiveError, match="changed before publication"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
            sign=True,
        )

    assert failed is True
    assert output.read_bytes() == b"existing archive\n"
    if previous_receipt is None:
        assert not receipt_path.exists()
    else:
        assert receipt_path.read_bytes() == previous_receipt
    assert not WorkspaceArchive.default_attestation_path(output).exists()


@pytest.mark.parametrize(
    "shared_basename",
    [False, True],
    ids=["default-output-names", "shared-output-basename"],
)
def test_workspace_archive_attestation_failure_restores_published_outputs(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shared_basename: bool,
) -> None:
    if shared_basename:
        output = tmp_path / "archive" / "workspace.tar.gz"
        receipt_path = tmp_path / "receipt" / "workspace.tar.gz"
        attestation_path = tmp_path / "attestation" / "workspace.tar.gz"
        for parent in (output.parent, receipt_path.parent, attestation_path.parent):
            parent.mkdir()
        receipt: bool | Path = receipt_path
        attestation = attestation_path
    else:
        output = tmp_path / "workspace.tar.gz"
        receipt_path = ArchiveReceipt.default_path(output)
        attestation_path = WorkspaceArchive.default_attestation_path(output)
        receipt = True
        attestation = None
    existing = {
        output: b"existing archive\n",
        receipt_path: b"existing receipt\n",
        attestation_path: b"existing attestation\n",
    }
    for path, content in existing.items():
        path.write_bytes(content)

    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        lambda payload: '{"bundle": true}',
    )

    def fail_attestation_publication(self, bundle: str) -> Path:
        raise AttestationError("attestation publication failed")

    monkeypatch.setattr(
        attestations_module.AttestationOutput,
        "write",
        fail_attestation_publication,
    )

    with pytest.raises(AttestationError, match="attestation publication failed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=receipt,
            sign=True,
            attestation=attestation,
        )

    assert {path: path.read_bytes() for path in existing} == existing


def test_workspace_archive_cleanup_reports_recovery_and_continues(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt_path = ArchiveReceipt.default_path(output)
    attestation_path = WorkspaceArchive.default_attestation_path(output)
    recovery = tmp_path / ".workspace.receipt.json.recovery.rollback"
    claimant = b"claimant receipt\n"
    monkeypatch.setattr(
        attestations_module,
        "sign_attestation_payload",
        lambda payload: '{"bundle": true}',
    )
    original_require = WorkspaceArchive.require_published_output
    original_remove = archive_module.remove_file_generation

    def fail_final_attestation_validation(
        cls: type[WorkspaceArchive],
        path: Path,
        **kwargs: Any,
    ) -> None:
        if kwargs["label"] == "Attestation output":
            raise ArchiveError("final output validation failed")
        original_require(path, **kwargs)

    def retain_receipt_recovery(path: Path, *args: Any, **kwargs: Any) -> bool:
        if path == receipt_path:
            path.rename(recovery)
            path.write_bytes(claimant)
            raise FileRecoveryError(path, recovery, "Receipt removal failed.")
        return original_remove(path, *args, **kwargs)

    monkeypatch.setattr(
        WorkspaceArchive,
        "require_published_output",
        classmethod(fail_final_attestation_validation),
    )
    monkeypatch.setattr(
        archive_module,
        "remove_file_generation",
        retain_receipt_recovery,
    )

    with pytest.raises(ArchiveError) as exc_info:
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
            sign=True,
        )

    message = str(exc_info.value)
    assert "final output validation failed" in message
    assert str(recovery) in message
    assert receipt_path.read_bytes() == claimant
    assert recovery.is_file()
    assert not output.exists()
    assert not attestation_path.exists()


def test_workspace_archive_receipt_rejects_replacement_after_write(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "workspace.tar.gz"
    receipt_path = ArchiveReceipt.default_path(output)
    replacement_content = b'{"attacker": true}\n'
    original_write = ArchiveReceipt.write

    def replace_receipt_after_write(
        self: ArchiveReceipt,
        path: Path,
        **kwargs: object,
    ) -> Path:
        result = original_write(self, path, **kwargs)
        replacement = tmp_path / "replacement-receipt.json"
        replacement.write_bytes(replacement_content)
        os.replace(replacement, path)
        return result

    monkeypatch.setattr(ArchiveReceipt, "write", replace_receipt_after_write)

    with pytest.raises(ArchiveHashMismatchError):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            receipt=True,
        )

    assert not output.exists()
    assert receipt_path.read_bytes() == replacement_content


def test_workspace_archive_receipt_failure_keeps_published_lock(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lockfile = workspace_archive_project / "conda.lock"
    generated_lock = (
        f"version: 1\nenvironments:\n  default:\n    channels: []\n"
        f"    packages:\n      {conda_context.subdir}: []\npackages: []\n"
    )
    monkeypatch.setattr(
        "conda_workspaces.resolver.resolve_all_environments",
        lambda config, platform: {"default": object()},
    )
    monkeypatch.setattr(
        "conda_workspaces.lockfile.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: generated_lock,
    )

    def fail_write(
        _self: ArchiveReceipt,
        _path: Path,
        **_kwargs: object,
    ) -> Path:
        raise RuntimeError("receipt failed")

    monkeypatch.setattr(
        ArchiveReceipt,
        "write",
        fail_write,
    )
    output = tmp_path / "workspace.tar.gz"
    receipt = output.with_name(f"{output.name}.receipt.json")

    with pytest.raises(RuntimeError, match="receipt failed"):
        WorkspaceArchive.create(
            workspace=workspace_archive_project,
            output=output,
            lock=True,
            receipt=True,
        )

    assert lockfile.read_text(encoding="utf-8") == generated_lock
    assert not output.exists()
    assert not receipt.exists()


@pytest.mark.parametrize("dry_run", [False, True], ids=["create", "dry-run"])
def test_workspace_archive_failure_keeps_only_published_lock(
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
    generated_lock = f"""\
version: 1
environments:
  default:
    channels: []
    packages:
      {conda_context.subdir}: []
packages: []
"""
    monkeypatch.setattr(
        "conda_workspaces.resolver.resolve_all_environments",
        lambda config, platform: {"default": object()},
    )
    monkeypatch.setattr(
        "conda_workspaces.lockfile.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: generated_lock,
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

    after = snapshot_tree(tmp_path)
    if not dry_run:
        after.pop("workspace/conda.lock")
        after.pop("workspace/.conda/workspace.lock")
        after.pop("workspace/.conda")
    assert after == before
    if dry_run:
        assert not lockfile.exists()
    else:
        assert lockfile.read_text(encoding="utf-8") == generated_lock
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


def test_workspace_archive_extract_verifies_signed_receipt(
    signed_receipt_archive: tuple[WorkspaceArchive, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, payload = signed_receipt_archive
    expected_signer = SignerPolicy("expected identity", "expected issuer")
    authenticated_signer = SignerIdentity("expected identity", "expected issuer")
    calls: list[bytes] = []

    def verify(bundle: bytes) -> VerifiedStatement:
        calls.append(bundle)
        return verified_receipt(payload, authenticated_signer)

    monkeypatch.setattr(attestations_module, "verify_attestation_bundle", verify)

    result = archive.extract(
        target=tmp_path / "extracted",
        verify_attestation=True,
        expected_signer=expected_signer,
    )

    assert calls == [b'{"bundle": true}\n']
    assert result.verified is True
    assert result.receipt_path is None
    assert result.attestation_verified is True
    assert result.attestation_path == archive.attestation_path
    assert (result.target / "src" / "app.py").is_file()


def test_workspace_archive_extract_rejects_mismatched_unsigned_receipt(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
    )
    assert created.receipt_path is not None
    statement = json.loads(created.receipt_path.read_text(encoding="utf-8"))
    statement["predicate"]["archive"]["options"]["bundle"] = True
    signed_payload = (json.dumps(statement, sort_keys=True) + "\n").encode("utf-8")
    attestation_path = WorkspaceArchive.default_attestation_path(created.path)
    attestation_path.write_text('{"bundle": true}\n', encoding="utf-8")
    archive = WorkspaceArchive(
        created.path,
        receipt=created.receipt_path,
        attestation=attestation_path,
    )
    monkeypatch.setattr(
        attestations_module,
        "verify_attestation_bundle",
        lambda bundle: verified_receipt(
            signed_payload,
            SignerIdentity("expected", "issuer"),
        ),
    )

    with pytest.raises(ArchiveError, match="does not match the signed"):
        archive.extract(
            target=tmp_path / "extracted",
            verify_attestation=True,
            expected_signer=SignerPolicy("expected", "issuer"),
        )

    assert not (tmp_path / "extracted").exists()


def test_workspace_archive_extract_requires_authorized_signer(
    signed_receipt_archive: tuple[WorkspaceArchive, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, payload = signed_receipt_archive

    monkeypatch.setattr(
        attestations_module,
        "verify_attestation_bundle",
        lambda bundle: verified_receipt(
            payload,
            SignerIdentity("authenticated", "issuer"),
        ),
    )

    with pytest.raises(AttestationError, match="does not match"):
        archive.extract(
            target=tmp_path / "extracted",
            verify_attestation=True,
            expected_signer=SignerPolicy("expected", "issuer"),
        )

    assert not (tmp_path / "extracted").exists()


def test_workspace_archive_extract_verifies_signed_receipt_before_inspection(
    signed_receipt_archive: tuple[WorkspaceArchive, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, payload = signed_receipt_archive
    archive.path.write_bytes(archive.path.read_bytes() + b"tampered")
    monkeypatch.setattr(
        attestations_module,
        "verify_attestation_bundle",
        lambda bundle: verified_receipt(
            payload,
            SignerIdentity("expected", "issuer"),
        ),
    )

    def fail_inspection(path: Path) -> dict[str, object]:
        raise AssertionError(f"archive was inspected before verification: {path}")

    monkeypatch.setattr(archive_module, "inspect_archive", fail_inspection)

    with pytest.raises(ArchiveHashMismatchError):
        archive.extract(
            target=tmp_path / "extracted",
            verify_attestation=True,
            expected_signer=SignerPolicy("expected", "issuer"),
            dry_run=True,
        )


def test_workspace_archive_extract_uses_one_immutable_snapshot(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
    )
    expected_manifest = (workspace_archive_project / "conda.toml").read_bytes()
    replacement = tmp_path / "replacement.tar.gz"
    with tarfile.open(replacement, "w:gz") as tf:
        content = b"[workspace]\nname = 'replacement'\n"
        member = tarfile.TarInfo("conda.toml")
        member.size = len(content)
        tf.addfile(member, io.BytesIO(content))
    original_snapshot = WorkspaceArchive.snapshot_archive
    replaced = False

    def replace_after_snapshot(source: Path, destination: Path) -> None:
        nonlocal replaced
        original_snapshot(source, destination)
        replacement.replace(source)
        replaced = True

    monkeypatch.setattr(
        WorkspaceArchive,
        "snapshot_archive",
        staticmethod(replace_after_snapshot),
    )
    target = tmp_path / "extracted"

    result = archive.extract(target=target)

    assert replaced is True
    assert result.verified is True
    assert (target / "conda.toml").read_bytes() == expected_manifest
    assert result.receipt_path == archive.receipt_path
    assert (result.target / "src" / "app.py").is_file()


def test_workspace_archive_snapshot_rejects_oversized_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "archive.tar"
    source.write_bytes(b"12345")
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_RAW_BYTES", 4)

    with pytest.raises(ArchiveError, match="maximum size"):
        WorkspaceArchive.snapshot_archive(source, tmp_path / "snapshot.tar")


@pytest.mark.parametrize(
    "operation",
    [
        "read",
        "read1",
        "readall",
        "readline",
        "readinto",
        "readinto1",
        "iterate",
        "peek",
    ],
    ids=[
        "read",
        "read1",
        "readall",
        "readline",
        "readinto",
        "readinto1",
        "iteration",
        "peek",
    ],
)
def test_open_stable_regular_file_rejects_growth_beyond_maximum(
    tmp_path: Path,
    operation: str,
) -> None:
    source = tmp_path / "archive.tar"
    source.write_bytes(b"A")

    with pytest.raises(ArchiveError, match="maximum size"):
        with archive_module.open_stable_regular_file(
            source,
            label="Archive",
            maximum_bytes=1,
        ) as stream:
            with source.open("ab") as output:
                output.write(b"B\n")
            if operation in {"readinto", "readinto1"}:
                getattr(stream, operation)(bytearray(3))
            elif operation == "iterate":
                next(iter(stream))
            else:
                getattr(stream, operation)()


@pytest.mark.parametrize(
    ("stream_content", "match", "snapshot_content"),
    [
        (b"BBBB", "changed while it was snapshotted", b"BBBB"),
        (b"AAAAA", "maximum size", None),
    ],
    ids=["same-generation-rewrite", "growth"],
)
def test_workspace_archive_snapshot_output_rejects_changed_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream_content: bytes,
    match: str,
    snapshot_content: bytes | None,
) -> None:
    source = tmp_path / "archive.tar"
    destination = tmp_path / "snapshot.tar"
    content = b"AAAA"
    source.write_bytes(content)
    generation = paths_module.regular_file_generation(source)
    assert generation is not None

    @contextmanager
    def changed_stream(
        path: Path,
        *,
        label: str,
        maximum_bytes: int | None = None,
    ) -> Iterator[io.BytesIO]:
        assert path == source
        assert label == "Archive"
        assert maximum_bytes == len(content)
        yield io.BytesIO(stream_content)

    monkeypatch.setattr(
        archive_module,
        "open_stable_regular_file",
        changed_stream,
    )

    with pytest.raises(ArchiveError, match=match):
        WorkspaceArchive.snapshot_output(
            source,
            destination,
            expected_generation=generation,
            expected_sha256=hashlib.sha256(content).hexdigest(),
            maximum_bytes=len(content),
            label="Archive",
        )

    assert source.read_bytes() == content
    if snapshot_content is None:
        assert not destination.exists()
    else:
        assert destination.read_bytes() == snapshot_content


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_workspace_archive_snapshot_rejects_fifo_without_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "archive.tar"
    os.mkfifo(source)
    original_open = os.open
    opened = False

    def require_nonblocking_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal opened
        if Path(path).name == source.name and dir_fd is not None:
            opened = True
            assert flags & os.O_NONBLOCK
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(archive_module.os, "open", require_nonblocking_open)

    with pytest.raises(ArchiveError, match="stable regular file"):
        WorkspaceArchive.snapshot_archive(source, tmp_path / "snapshot.tar")

    assert opened


def test_workspace_archive_snapshot_rejects_same_length_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "archive.tar"
    source.write_bytes(b"AAAA")
    original_lstat = Path.lstat
    replaced = False

    def rewrite_after_stat(self: Path):
        nonlocal replaced
        result = original_lstat(self)
        if self == source and not replaced:
            replaced = True
            source.write_bytes(b"BBBB")
            os.utime(
                source,
                ns=(result.st_atime_ns, result.st_mtime_ns + 1_000_000_000),
            )
        return result

    monkeypatch.setattr(Path, "lstat", rewrite_after_stat)

    with pytest.raises(ArchiveError, match="changed while reading|stable regular file"):
        WorkspaceArchive.snapshot_archive(source, tmp_path / "snapshot.tar")

    assert replaced is True


def test_workspace_archive_extract_verifies_receipt_before_inspection(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
    )
    archive.path.write_bytes(archive.path.read_bytes() + b"tampered")

    def fail_inspection(path: Path) -> dict[str, object]:
        raise AssertionError(f"archive was inspected before verification: {path}")

    monkeypatch.setattr(archive_module, "inspect_archive", fail_inspection)

    with pytest.raises(ArchiveHashMismatchError):
        archive.extract(
            target=tmp_path / "extracted",
            require_sha256=True,
            dry_run=True,
        )


def test_workspace_archive_extract_rejects_existing_empty_target(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    target = tmp_path / "extracted"
    target.mkdir(mode=0o700)
    os.utime(target, ns=(1_700_000_000_123_456_789, 1_700_000_001_987_654_321))
    expected = target.stat()
    with pytest.raises(ArchiveError, match="existing target"):
        archive.extract(target=target)

    actual = target.stat()
    assert actual.st_ino == expected.st_ino
    assert actual.st_uid == expected.st_uid
    assert actual.st_gid == expected.st_gid
    assert actual.st_mode == expected.st_mode
    assert actual.st_atime_ns == expected.st_atime_ns
    assert actual.st_mtime_ns == expected.st_mtime_ns
    assert not any(target.iterdir())


def test_workspace_archive_extract_rejects_concurrent_target_creation(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    target = tmp_path / "extracted"
    original_rename = archive_module.rename_noreplace
    raced = False

    def create_target(*args, **kwargs):
        nonlocal raced
        if not raced:
            target.mkdir()
            raced = True
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(archive_module, "rename_noreplace", create_target)

    with pytest.raises(ArchiveError, match="target changed"):
        archive.extract(target=target)

    assert raced is True
    assert not any(target.iterdir())


def test_workspace_archive_extract_rejects_target_swap(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    target = tmp_path / "extracted"
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


def test_workspace_archive_extract_anchors_target_parent_during_promotion(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not paths_module.supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    parent = tmp_path / "target-parent"
    target = parent / "extracted"
    displaced = tmp_path / "displaced-target-parent"
    external_parent = tmp_path / "external-target-parent"
    external_parent.mkdir()
    original_rename = archive_module.rename_noreplace
    raced = False

    def replace_parent(*args, **kwargs):
        nonlocal raced
        destination = args[1]
        if Path(destination).name == target.name and not raced:
            raced = True
            parent.rename(displaced)
            parent.symlink_to(external_parent, target_is_directory=True)
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(archive_module, "rename_noreplace", replace_parent)

    with pytest.raises(ArchiveError, match="target changed"):
        archive.extract(target=target)

    assert raced
    assert not (external_parent / target.name).exists()
    assert (displaced / target.name).is_dir()


def test_workspace_archive_extract_rejects_live_target_replacement(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    target = tmp_path / "extracted"
    displaced = tmp_path / "published-extracted"
    original_rename = archive_module.rename_noreplace
    raced = False

    def replace_published_target(*args, **kwargs) -> None:
        nonlocal raced
        original_rename(*args, **kwargs)
        destination = args[1]
        if Path(destination).name == target.name and not raced:
            target.rename(displaced)
            target.mkdir()
            raced = True

    monkeypatch.setattr(archive_module, "rename_noreplace", replace_published_target)

    with pytest.raises(ArchiveError, match="target changed"):
        archive.extract(target=target)

    assert raced
    assert not any(target.iterdir())
    assert (displaced / "conda.toml").is_file()


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
    shutil.rmtree(target)
    repeated = archives[0].extract(target=target, package_cache=package_cache)

    assert first.primed_packages == 1
    assert second.primed_packages == 1
    assert repeated.primed_packages == 0
    package_names = {package_name for _, package_name, _ in packages}
    assert {path.name for path in package_cache.iterdir()} == {
        *package_names,
        "urls.txt",
        *(package_name.removesuffix(".conda") for package_name in package_names),
    }
    for package_name in package_names:
        assert (
            package_cache
            / package_name.removesuffix(".conda")
            / "info"
            / "repodata_record.json"
        ).is_file()


def test_workspace_archive_extract_rejects_cache_stem_collisions_before_publication(
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
) -> None:
    root = tmp_path / "collision-workspace"
    root.mkdir()
    (root / "conda.toml").write_text(
        f"""\
[workspace]
name = "collision"
channels = ["conda-forge"]
platforms = ["{conda_context.subdir}"]
""",
        encoding="utf-8",
    )
    packages = [
        ("demo-1.0-h0.conda", b"conda package"),
        ("demo-1.0-h0.tar.bz2", b"tar package"),
    ]
    package_records = []
    package_paths = []
    for filename, content in packages:
        url = (
            f"https://conda.anaconda.org/conda-forge/{conda_context.subdir}/{filename}"
        )
        package_records.append(
            f"""\
  - conda: {url}
    sha256: {hashlib.sha256(content).hexdigest()}
    name: demo
    version: "1.0"
    build: h0
    subdir: {conda_context.subdir}
    depends: []
"""
        )
        package_path = tmp_path / "source-cache" / filename
        package_path.parent.mkdir(exist_ok=True)
        package_path.write_bytes(content)
        package_paths.append(package_path)
    environment_records = "\n".join(
        f"        - conda: https://conda.anaconda.org/conda-forge/{conda_context.subdir}/{filename}"
        for filename, _ in packages
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
{environment_records}
packages:
{"".join(package_records)}""",
        encoding="utf-8",
    )
    archive_path = tmp_path / "collision.tar.gz"
    archive_config = ArchiveConfig()
    create_archive(
        root,
        archive_path,
        archive_config,
        bundle_packages=package_paths,
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
    archive = WorkspaceArchive(archive_path, receipt=receipt_path)
    before = snapshot_tree(tmp_path)

    with pytest.raises(ArchiveError, match="same extracted cache entry"):
        archive.extract(
            target=tmp_path / "extracted",
            package_cache=tmp_path / "package-cache",
        )

    assert snapshot_tree(tmp_path) == before


@pytest.mark.parametrize(
    "cache_entry",
    ["mismatched-record", "entry-symlink", "info-symlink"],
    ids=["mismatched-record", "entry-symlink", "info-symlink"],
)
def test_workspace_archive_extract_rejects_conflicting_cache_records(
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
    receipt_bundled_archive_factory: Callable[
        [str, str, bytes, str | None], WorkspaceArchive
    ],
    cache_entry: str,
) -> None:
    package_name = "demo-1.0-h0.conda"
    archive = receipt_bundled_archive_factory(
        "cache-record",
        package_name,
        b"package",
        None,
    )
    cache = tmp_path / "package-cache"
    first_target = tmp_path / "first"
    archive.extract(target=first_target, package_cache=cache)
    shutil.rmtree(first_target)

    extracted = cache / package_name.removesuffix(".conda")
    if cache_entry == "mismatched-record":
        record_path = extracted / "info" / "repodata_record.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["url"] = "https://example.invalid/replaced.conda"
        record_path.write_text(json.dumps(record), encoding="utf-8")
    elif cache_entry == "entry-symlink":
        shutil.rmtree(extracted)
        extracted.symlink_to(tmp_path / "outside-cache")
    else:
        outside_info = tmp_path / "outside-info"
        shutil.copytree(extracted / "info", outside_info)
        shutil.rmtree(extracted / "info")
        (extracted / "info").symlink_to(outside_info, target_is_directory=True)
    before = snapshot_tree(tmp_path)

    with pytest.raises(ArchiveError, match="Package cache entry conflicts"):
        archive.extract(
            target=tmp_path / "second",
            package_cache=cache,
        )

    assert snapshot_tree(tmp_path) == before


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


def test_workspace_archive_extract_rejects_cache_destination_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_bundled_archive_factory: Callable[
        [str, str, bytes, str | None], WorkspaceArchive
    ],
) -> None:
    package_name = "demo-1.0-h0.conda"
    archive = receipt_bundled_archive_factory(
        "valid",
        package_name,
        b"package",
        None,
    )
    cache = tmp_path / "package-cache"
    destination = cache / package_name
    outside = tmp_path / "outside-package"
    outside.write_bytes(b"keep")
    original_rename = paths_module.rename_noreplace
    raced = False

    def race_rename(source, target, **kwargs) -> None:
        nonlocal raced
        if Path(target).name == destination.name and not raced:
            raced = True
            destination.symlink_to(outside)
        original_rename(source, target, **kwargs)

    monkeypatch.setattr(paths_module, "rename_noreplace", race_rename)

    with pytest.raises(ArchiveError, match="destination changed"):
        archive.extract(
            target=tmp_path / "extracted",
            package_cache=cache,
        )

    assert raced
    assert outside.read_bytes() == b"keep"
    assert destination.is_symlink()


def test_workspace_archive_extract_anchors_cache_parent_during_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_bundled_archive_factory: Callable[
        [str, str, bytes, str | None], WorkspaceArchive
    ],
) -> None:
    if not paths_module.supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    package_name = "demo-1.0-h0.conda"
    archive = receipt_bundled_archive_factory(
        "valid",
        package_name,
        b"package",
        None,
    )
    cache = tmp_path / "package-cache"
    displaced = tmp_path / "displaced-package-cache"
    external_cache = tmp_path / "external-package-cache"
    external_cache.mkdir()
    original_rename = paths_module.rename_noreplace
    raced = False

    def replace_cache_parent(source, destination, **kwargs) -> None:
        nonlocal raced
        if (
            Path(destination).name == package_name
            and kwargs.get("destination_dir_fd") is not None
            and not raced
        ):
            raced = True
            cache.rename(displaced)
            cache.symlink_to(external_cache, target_is_directory=True)
        original_rename(source, destination, **kwargs)

    monkeypatch.setattr(paths_module, "rename_noreplace", replace_cache_parent)

    with pytest.raises(ArchiveError, match="cannot be published safely"):
        archive.extract(
            target=tmp_path / "extracted",
            package_cache=cache,
        )

    assert raced
    assert not (external_cache / package_name).exists()
    assert (displaced / package_name).read_bytes() == b"package"


@pytest.mark.parametrize(
    ("race", "error", "match"),
    [
        pytest.param(
            "symlink",
            ArchiveError,
            "source",
            id="symlink-source",
        ),
        pytest.param(
            "content",
            ArchiveHashMismatchError,
            "Hash mismatch",
            id="changed-content",
        ),
        pytest.param(
            "fifo",
            ArchiveError,
            "source",
            id="fifo-source",
            marks=pytest.mark.skipif(
                not hasattr(os, "mkfifo"),
                reason="FIFO requires POSIX",
            ),
        ),
    ],
)
def test_workspace_archive_extract_revalidates_cache_source_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_bundled_archive_factory: Callable[
        [str, str, bytes, str | None], WorkspaceArchive
    ],
    race: str,
    error: type[ArchiveError],
    match: str,
) -> None:
    package_name = "demo-1.0-h0.conda"
    archive = receipt_bundled_archive_factory(
        "valid",
        package_name,
        b"package",
        None,
    )
    target = tmp_path / "extracted"
    cache = tmp_path / "package-cache"
    destination = cache / package_name
    outside = tmp_path / "outside-package"
    outside.write_bytes(b"package")
    original_open = archive_module.open_stable_regular_file
    raced = False

    @contextmanager
    def race_source_open(
        path: Path,
        *,
        label: str,
        maximum_bytes: int | None = None,
    ):
        nonlocal raced
        if label == "Bundled package source" and not raced:
            raced = True
            if race == "symlink":
                path.unlink()
                path.symlink_to(outside)
            elif race == "fifo":
                path.unlink()
                os.mkfifo(path)
            else:
                path.write_bytes(b"tampered")
        with original_open(
            path,
            label=label,
            maximum_bytes=maximum_bytes,
        ) as stream:
            yield stream

    monkeypatch.setattr(archive_module, "open_stable_regular_file", race_source_open)

    with pytest.raises(error, match=match):
        archive.extract(target=target, package_cache=cache)

    assert raced
    assert not destination.exists()
    assert outside.read_bytes() == b"package"


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


@pytest.mark.parametrize(
    "supports_verified_workspace",
    [True, False],
    ids=["compatible", "incompatible"],
)
def test_workspace_archive_receipt_validates_public_handler_contract(
    workspace_archive_project: Path,
    tmp_path: Path,
    supports_verified_workspace: bool,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
    )
    target = tmp_path / "extracted"
    calls: list[VerifiedArchiveWorkspace] = []

    def compatible_handler(
        workspace: Path,
        environment: str | None,
        install_prefix: Path | None,
        target_prefix_override: str | None,
        *,
        verified_workspace: VerifiedArchiveWorkspace,
    ) -> int:
        assert workspace == target.resolve()
        assert environment is None
        assert install_prefix is None
        assert target_prefix_override is None
        calls.append(verified_workspace)
        return 0

    def incompatible_handler(
        workspace: Path,
        environment: str | None,
        install_prefix: Path | None,
        target_prefix_override: str | None,
    ) -> int:
        raise AssertionError(
            (workspace, environment, install_prefix, target_prefix_override)
        )

    handler = (
        compatible_handler if supports_verified_workspace else incompatible_handler
    )
    if supports_verified_workspace:
        result = archive.install(target=target, install_handler=handler)

        assert result.return_code == 0
        assert len(calls) == 1
        assert (
            calls[0].manifest_bytes
            == (workspace_archive_project / "conda.toml").read_bytes()
        )
        assert (
            calls[0].lockfile_bytes
            == (workspace_archive_project / "conda.lock").read_bytes()
        )
        assert target.is_dir()
    else:
        with pytest.raises(
            ArchiveError,
            match="must accept the verified_workspace keyword argument",
        ):
            archive.install(target=target, install_handler=handler)

        assert calls == []
        assert not target.exists()


def test_workspace_archive_install_passes_receipt_verified_workspace(
    workspace_archive_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_manifest = (workspace_archive_project / "conda.toml").read_bytes()
    expected_lockfile = (workspace_archive_project / "conda.lock").read_bytes()
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
        receipt=True,
    )
    target = tmp_path / "extracted"
    replacement_manifest = b"later manifest bytes\n"
    replacement_lockfile = b"later lockfile bytes\n"
    original_rename = archive_module.rename_noreplace

    def replace_after_publish(
        source: str | Path,
        destination: str | Path,
        *,
        source_dir_fd: int | None = None,
        destination_dir_fd: int | None = None,
    ) -> None:
        original_rename(
            source,
            destination,
            source_dir_fd=source_dir_fd,
            destination_dir_fd=destination_dir_fd,
        )
        if Path(destination).name == target.name:
            (target / "conda.toml").write_bytes(replacement_manifest)
            (target / "conda.lock").write_bytes(replacement_lockfile)

    monkeypatch.setattr(archive_module, "rename_noreplace", replace_after_publish)
    expected_lockfile_data = archive_module.load_lockfile_data(expected_lockfile)
    loaded_lockfiles: list[bytes] = []
    install_calls: list[
        tuple[bool, str, str | None, str | None, dict[str, Any] | None]
    ] = []
    original_load_lockfile_data = archive_module.load_lockfile_data

    def record_load_lockfile_data(content: str | bytes) -> dict[str, Any]:
        assert isinstance(content, bytes)
        loaded_lockfiles.append(content)
        return original_load_lockfile_data(content)

    def record_install(
        ctx: WorkspaceContext,
        environment: str,
        *,
        prefix: Path | None = None,
        target_prefix_override: str | Path | None = None,
        dry_run: bool = False,
        lockfile_data: dict[str, Any] | None = None,
        **kwargs: object,
    ) -> None:
        assert prefix is None
        assert target_prefix_override is None
        assert kwargs == {}
        install_calls.append(
            (
                dry_run,
                environment,
                ctx.config.name,
                ctx.config._manifest_text,
                lockfile_data,
            )
        )

    monkeypatch.setattr(
        archive_module,
        "load_lockfile_data",
        record_load_lockfile_data,
    )
    monkeypatch.setattr(lockfile_module, "install_from_lockfile", record_install)

    result = archive.install(target=target)

    assert result.return_code == 0
    assert loaded_lockfiles == [expected_lockfile, expected_lockfile]
    assert install_calls == [
        (
            True,
            "default",
            "archive-api-test",
            expected_manifest.decode("utf-8"),
            expected_lockfile_data,
        ),
        (
            False,
            "default",
            "archive-api-test",
            expected_manifest.decode("utf-8"),
            expected_lockfile_data,
        ),
    ]
    assert (target / "conda.toml").read_bytes() == replacement_manifest
    assert (target / "conda.lock").read_bytes() == replacement_lockfile


@pytest.mark.parametrize(
    "workflow",
    ["combined", "two-step"],
    ids=["combined", "two-step"],
)
def test_workspace_archive_installs_bundle_from_empty_cache_offline(
    installable_bundled_archive: WorkspaceArchive,
    tmp_path: Path,
    workflow: str,
) -> None:
    cache = tmp_path / f"{workflow}-cache"
    target = tmp_path / f"{workflow}-workspace"

    PackageCacheData.clear()
    try:
        with (
            conda_context._override("_pkgs_dirs", (str(cache),)),
            conda_context._override("offline", True),
        ):
            if workflow == "combined":
                result = installable_bundled_archive.install(
                    target=target,
                    package_cache=cache,
                )
                assert result.return_code == 0
                assert result.primed_packages == 1
            else:
                extracted = installable_bundled_archive.extract(
                    target=target,
                    package_cache=cache,
                )
                assert extracted.primed_packages == 1
                assert (
                    WorkspaceArchive.install_from_lockfile(target, None, None, None)
                    == 0
                )
    finally:
        PackageCacheData.clear()

    assert (
        target / ".conda" / "envs" / "default" / "share" / "offline-demo.txt"
    ).read_text(encoding="utf-8") == "installed offline\n"
    record = json.loads(
        (cache / "offline-demo-1.0-h0" / "info" / "repodata_record.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["url"].endswith(
        f"/{conda_context.subdir}/offline-demo-1.0-h0.tar.bz2"
    )
    assert (
        record["sha256"]
        == hashlib.sha256(
            (cache / "offline-demo-1.0-h0.tar.bz2").read_bytes()
        ).hexdigest()
    )


def test_workspace_archive_install_rejects_cache_archive_changed_during_preflight(
    installable_bundled_archive: WorkspaceArchive,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import conda_workspaces.lockfile as lockfile_module

    cache = tmp_path / "package-cache"
    target = tmp_path / "workspace"
    package = cache / "offline-demo-1.0-h0.tar.bz2"
    handler_calls: list[Path] = []

    def change_cached_package(*_args: object, **_kwargs: object) -> None:
        content = package.read_bytes()
        package.write_bytes(b"x" * len(content))

    def record_handler(
        workspace: Path,
        *_args: object,
        verified_workspace: object | None = None,
    ) -> int:
        del verified_workspace
        handler_calls.append(workspace)
        return 0

    monkeypatch.setattr(
        lockfile_module,
        "install_from_lockfile",
        change_cached_package,
    )

    with pytest.raises(ArchiveHashMismatchError):
        installable_bundled_archive.install(
            target=target,
            package_cache=cache,
            install_handler=record_handler,
        )

    assert handler_calls == []
    assert not target.exists()


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


def test_workspace_archive_install_rejects_symlinked_dest(
    workspace_archive_project: Path,
    tmp_path: Path,
) -> None:
    archive = WorkspaceArchive.create(
        workspace=workspace_archive_project,
        output=tmp_path / "workspace.tar.gz",
    )
    external = tmp_path / "external"
    external.mkdir()
    dest = tmp_path / "dest"
    dest.symlink_to(external, target_is_directory=True)

    with pytest.raises(ArchiveError, match="--dest cannot be a symbolic link"):
        archive.install(
            target=tmp_path / "extracted",
            environment="default",
            prefix="/opt/runtime",
            dest=dest,
            dry_run=True,
        )

    assert not any(external.iterdir())


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


def test_url_to_filename_error_redacts_embedded_and_relative_credentials() -> None:
    value = (
        "download https://user:ABSOLUTE-LEAK@packages.example.test/repodata.json "
        "from t/RELATIVE-LEAK/private"
    )

    with pytest.raises(ArchiveError) as caught:
        url_to_filename(value)

    message = str(caught.value)
    assert "ABSOLUTE-LEAK" not in message
    assert "RELATIVE-LEAK" not in message
