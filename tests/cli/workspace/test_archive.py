"""Tests for conda workspace archive and unarchive."""

from __future__ import annotations

import hashlib
import json
import tarfile
from io import StringIO
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from conda_workspaces.archive import (
    create_archive,
    extract_verified_archive,
    file_contains_bytes,
    is_absolute_runtime_prefix,
    receipt_environment_prefixes,
    resolve_receipt_path,
    runtime_prefix_relative_path,
    scan_prefix_references,
)
from conda_workspaces.cli.workspace.archive import execute_archive, execute_unarchive
from conda_workspaces.exceptions import ArchiveError, AttestationError
from conda_workspaces.models import ArchiveConfig
from conda_workspaces.receipts import ArchiveReceipt

from ..conftest import make_args

if TYPE_CHECKING:
    from collections.abc import Callable

_ARCHIVE_DEFAULTS = {
    "file": None,
    "output": None,
    "bundle": False,
    "lock": False,
    "exclude": None,
    "receipt": None,
    "sign": False,
    "identity_token": None,
    "dry_run": False,
    "json": False,
}

_UNARCHIVE_DEFAULTS = {
    "file": None,
    "archive_path": None,
    "target": None,
    "install": False,
    "no_install": False,
    "environment": None,
    "prefix": None,
    "dest": None,
    "receipt": None,
    "require_sha256": False,
    "verify": False,
    "cert_identity": None,
    "cert_oidc_issuer": None,
    "dry_run": False,
    "json": False,
}


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("/opt/runtime", True),
        ("/usr/local/vela", True),
        ("C:/vela/runtime", True),
        ("C:\\vela\\runtime", True),
        ("relative/prefix", False),
        ("runtime", False),
    ],
)
def test_is_absolute_runtime_prefix(prefix: str, expected: bool) -> None:
    assert is_absolute_runtime_prefix(prefix) is expected


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("/opt/runtime", Path("opt") / "runtime"),
        ("/usr/local/vela", Path("usr") / "local" / "vela"),
        ("C:/vela/runtime", Path("vela") / "runtime"),
        ("C:\\vela\\runtime", Path("vela") / "runtime"),
    ],
)
def test_runtime_prefix_relative_path(prefix: str, expected: Path) -> None:
    assert runtime_prefix_relative_path(prefix) == expected


@pytest.mark.parametrize(
    ("needle", "expected"),
    [
        (b"cde", True),
        (b"missing", False),
        (b"", False),
    ],
)
def test_file_contains_bytes(
    tmp_path: Path,
    needle: bytes,
    expected: bool,
) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"abcde")

    assert file_contains_bytes(path, needle, chunk_size=2) is expected


def test_scan_prefix_references_limits_matches(tmp_path: Path) -> None:
    prefix = tmp_path / "rootfs" / "opt" / "runtime"
    prefix.mkdir(parents=True)
    for index in range(3):
        (prefix / f"match-{index}.txt").write_text(str(prefix), encoding="utf-8")
    (prefix / "clean.txt").write_text("/opt/runtime", encoding="utf-8")
    (prefix / "nested").mkdir()

    matches, truncated = scan_prefix_references(prefix, prefix, limit=2)

    assert len(matches) == 2
    assert truncated is True
    assert all(path.name.startswith("match-") for path in matches)


@pytest.mark.parametrize(
    ("receipt", "expected"),
    [
        (None, None),
        (False, None),
        (True, "workspace.tar.gz.receipt.json"),
        (Path("custom.json"), "custom.json"),
        ("string.json", "string.json"),
    ],
    ids=["none", "false", "default", "path", "string"],
)
def test_resolve_receipt_path(
    tmp_path: Path,
    receipt: object,
    expected: str | None,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    result = resolve_receipt_path(archive_path, receipt)

    if expected is None:
        assert result is None
    elif receipt is True:
        assert result == tmp_path / expected
    else:
        assert result == Path(expected)


def test_resolve_receipt_path_rejects_invalid_value(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="Invalid --receipt value"):
        resolve_receipt_path(tmp_path / "workspace.tar.gz", object())


def test_receipt_environment_prefixes_records_external_prefix(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    prefixes = receipt_environment_prefixes(
        config_environments=["default", "runtime"],
        ctx_root=root,
        env_prefix=lambda name: (
            root / ".conda" / "envs" / name
            if name == "default"
            else Path("/opt/runtime")
        ),
    )

    assert prefixes == {
        "default": ".conda/envs/default",
        "runtime": "/opt/runtime",
    }


def test_receipt_environment_prefixes_normalizes_windows_external_prefix() -> None:
    prefixes = receipt_environment_prefixes(
        config_environments=["runtime"],
        ctx_root=PureWindowsPath("C:/workspace"),
        env_prefix=lambda name: PureWindowsPath("D:/runtime"),
    )

    assert prefixes == {"runtime": "D:/runtime"}


def test_execute_archive_sign_includes_attestation_sidecar(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "signed.tar.gz"

    def fake_write_workspace_attestation(**kwargs):
        path = archive_workspace / "conda.lock.sigstore.json"
        path.write_text('{"bundle":true}\n', encoding="utf-8")
        return path

    monkeypatch.setattr(
        "conda_workspaces.attestations.write_workspace_attestation",
        fake_write_workspace_attestation,
    )

    execute_archive(
        make_args(_ARCHIVE_DEFAULTS, output=archive, sign=True),
        console=Console(file=StringIO(), width=200, highlight=False),
    )

    with tarfile.open(archive, "r:gz") as tf:
        names = set(tf.getnames())

    assert "conda.lock.sigstore.json" in names


def test_execute_archive_identity_token_requires_sign(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)

    with pytest.raises(ArchiveError, match="requires --sign"):
        execute_archive(
            make_args(
                _ARCHIVE_DEFAULTS,
                output=tmp_path / "archive.tar.gz",
                identity_token="token",
            ),
            console=Console(file=StringIO(), width=200, highlight=False),
        )


@pytest.fixture
def archive_workspace(tmp_path: Path) -> Path:
    manifest = """\
[workspace]
name = "archive-test"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"
"""
    (tmp_path / "conda.toml").write_text(manifest, encoding="utf-8")
    (tmp_path / "conda.lock").write_text(
        "version: 1\nenvironments:\n  default:\n    channels:\n"
        "      - url: https://conda.anaconda.org/conda-forge/\n"
        "    packages:\n      linux-64: []\n      osx-arm64: []\npackages: []\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def bundled_cli_archive(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "conda.toml").write_text(
        """\
[workspace]
name = "archive-test"
channels = ["conda-forge"]
platforms = ["linux-64"]
""",
        encoding="utf-8",
    )
    package_name = "example-1.0-h123.conda"
    package_content = b"example package"
    sha256 = hashlib.sha256(package_content).hexdigest()
    (root / "conda.lock").write_text(
        f"""\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/linux-64/{package_name}
packages:
  - conda: https://conda.anaconda.org/conda-forge/linux-64/{package_name}
    sha256: {sha256}
    name: example
    version: "1.0"
    build: h123
    subdir: linux-64
    depends: []
""",
        encoding="utf-8",
    )
    package_cache = tmp_path / "package-cache"
    package_cache.mkdir()
    package_path = package_cache / package_name
    package_path.write_bytes(package_content)

    archive = tmp_path / "bundled.tar.gz"
    archive_config = ArchiveConfig()
    create_archive(root, archive, archive_config, bundle_packages=[package_path])
    receipt = ArchiveReceipt.build(
        root=root,
        archive_path=archive,
        archive_config=archive_config,
        manifest_path=root / "conda.toml",
        lockfile_path=root / "conda.lock",
        environment_prefixes={"default": ".conda/envs/default"},
        options={"bundle": True, "lock": False},
    )
    receipt.write(ArchiveReceipt.default_path(archive))
    return archive


def test_execute_archive_default(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    output = tmp_path / "out.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args = make_args(_ARCHIVE_DEFAULTS, output=output)
    result = execute_archive(args, console=console)

    assert result == 0
    assert output.is_file()
    with tarfile.open(output, "r:gz") as tf:
        names = tf.getnames()
    assert "conda.toml" in names
    assert "conda.lock" in names
    assert "src/app.py" in names


def test_execute_archive_no_output(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(archive_workspace)
    console = Console(file=StringIO(), width=200, highlight=False)

    args = make_args(_ARCHIVE_DEFAULTS)
    result = execute_archive(args, console=console)

    assert result == 0
    expected = archive_workspace / "archive-test.tar.zst"
    assert expected.is_file()


@pytest.mark.parametrize(
    "workspace_name",
    [
        "../escaped-output",
        "nested/archive-test",
        r"nested\archive-test",
        "/tmp/escaped-output",
        "C:escaped-output",
        "C:/escaped-output",
    ],
    ids=[
        "parent",
        "nested-posix",
        "nested-windows",
        "absolute",
        "windows-drive-relative",
        "windows-absolute",
    ],
)
def test_execute_archive_rejects_path_like_default_output_name(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    workspace_name: str,
) -> None:
    manifest = archive_workspace / "conda.toml"
    manifest.write_text(
        f"""\
[workspace]
name = '{workspace_name}'
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(archive_workspace)
    console = Console(file=StringIO(), width=200, highlight=False)

    with pytest.raises(ArchiveError, match="default archive filename"):
        execute_archive(make_args(_ARCHIVE_DEFAULTS), console=console)

    assert not (archive_workspace.parent / "escaped-output.tar.zst").exists()
    assert not (archive_workspace / "nested").exists()


def test_execute_archive_explicit_output_allows_path_like_workspace_name(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = archive_workspace / "conda.toml"
    manifest.write_text(
        """\
[workspace]
name = '../escaped-output'
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(archive_workspace)
    output = tmp_path / "chosen.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    result = execute_archive(
        make_args(_ARCHIVE_DEFAULTS, output=output),
        console=console,
    )

    assert result == 0
    assert output.is_file()


def test_execute_archive_exclude(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    output = tmp_path / "out.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args = make_args(_ARCHIVE_DEFAULTS, output=output, exclude=["src/**"])
    result = execute_archive(args, console=console)

    assert result == 0
    with tarfile.open(output, "r:gz") as tf:
        names = tf.getnames()
    assert "conda.toml" in names
    assert "src/app.py" not in names


@pytest.mark.parametrize(
    ("receipt", "expected_name"),
    [
        (True, "test.tar.gz.receipt.json"),
        ("custom-receipt.json", "custom-receipt.json"),
    ],
    ids=["default-path", "explicit-path"],
)
def test_execute_archive_receipt_path(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    receipt: object,
    expected_name: str,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)
    receipt_arg = tmp_path / receipt if isinstance(receipt, str) else receipt

    args = make_args(_ARCHIVE_DEFAULTS, output=archive, receipt=receipt_arg)
    result = execute_archive(args, console=console)

    assert result == 0
    receipt_path = tmp_path / expected_name
    data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert data["_type"] == "https://in-toto.io/Statement/v1"
    assert data["subject"][0]["name"] == "test.tar.gz"
    assert data["predicate"]["workspace"] == {
        "manifest": "conda.toml",
        "lockfile": "conda.lock",
    }


def test_execute_archive_receipt_path_cannot_be_archive_path(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args = make_args(_ARCHIVE_DEFAULTS, output=archive, receipt=archive)
    with pytest.raises(ArchiveError, match="Receipt path cannot be the archive path"):
        execute_archive(args, console=console)


@pytest.mark.parametrize(
    ("exclude", "match"),
    [
        ("conda.toml", "workspace manifest"),
        ("conda.lock", "workspace lockfile"),
    ],
    ids=["manifest", "lockfile"],
)
def test_execute_archive_receipt_requires_bound_files_in_archive(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exclude: str,
    match: str,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args = make_args(
        _ARCHIVE_DEFAULTS,
        output=archive,
        exclude=[exclude],
        receipt=True,
    )
    with pytest.raises(ArchiveError, match=match):
        execute_archive(args, console=console)

    assert not archive.exists()
    assert not ArchiveReceipt.default_path(archive).exists()


@pytest.mark.parametrize(
    ("exclude", "match"),
    [
        ("conda.toml", "workspace manifest"),
        ("conda.lock", "workspace lockfile"),
    ],
    ids=["manifest", "lockfile"],
)
def test_execute_archive_sign_requires_bound_files_in_archive(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exclude: str,
    match: str,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)
    calls: list[object] = []

    def fake_write_workspace_attestation(**kwargs):
        calls.append(kwargs)
        return archive_workspace / "conda.lock.sigstore.json"

    monkeypatch.setattr(
        "conda_workspaces.attestations.write_workspace_attestation",
        fake_write_workspace_attestation,
    )

    args = make_args(
        _ARCHIVE_DEFAULTS,
        output=archive,
        exclude=[exclude],
        sign=True,
    )
    with pytest.raises(ArchiveError, match=match):
        execute_archive(args, console=console)

    assert calls == []
    assert not archive.exists()


def test_execute_unarchive_basic(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    target = tmp_path / "extracted"
    args_u = make_args(_UNARCHIVE_DEFAULTS, archive_path=archive, target=target)
    result = execute_unarchive(args_u, console=console)

    assert result == 0
    assert (target / "conda.toml").is_file()
    assert (target / "conda.lock").is_file()
    assert (target / "src" / "app.py").is_file()


def test_execute_unarchive_receipt_default_path(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    execute_archive(
        make_args(_ARCHIVE_DEFAULTS, output=archive, receipt=True),
        console=console,
    )

    target = tmp_path / "extracted"
    result = execute_unarchive(
        make_args(
            _UNARCHIVE_DEFAULTS,
            archive_path=archive,
            target=target,
            receipt=True,
        ),
        console=console,
    )

    assert result == 0
    assert (target / "conda.toml").is_file()
    assert "Verified" in console.file.getvalue()


def test_execute_unarchive_receipt_detects_tampered_archive(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    execute_archive(
        make_args(_ARCHIVE_DEFAULTS, output=archive, receipt=True),
        console=console,
    )
    archive.write_bytes(archive.read_bytes() + b"tamper")

    with pytest.raises(ArchiveError, match="Hash mismatch"):
        execute_unarchive(
            make_args(
                _UNARCHIVE_DEFAULTS,
                archive_path=archive,
                target=tmp_path / "extracted",
                receipt=True,
            ),
            console=console,
        )


@pytest.mark.parametrize(
    ("receipt", "expected_primed"),
    [
        (None, False),
        (True, True),
    ],
    ids=["without-receipt", "with-receipt"],
)
def test_execute_unarchive_package_cache_priming_requires_receipt(
    bundled_cli_archive: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    receipt: object,
    expected_primed: bool,
) -> None:
    calls: list[tuple[Path, Path, bool]] = []

    def fake_prime_package_cache(
        extracted_dir: Path,
        cache_dir: Path,
        *,
        verified: bool = False,
    ) -> int:
        calls.append((extracted_dir, cache_dir, verified))
        return 1

    monkeypatch.setattr(
        "conda_workspaces.archive.prime_package_cache",
        fake_prime_package_cache,
    )

    target = tmp_path / f"extracted-{receipt or 'none'}"
    stream = StringIO()
    result = execute_unarchive(
        make_args(
            _UNARCHIVE_DEFAULTS,
            archive_path=bundled_cli_archive,
            target=target,
            receipt=receipt,
        ),
        console=Console(file=stream, width=200, highlight=False),
    )

    assert result == 0
    if expected_primed:
        assert len(calls) == 1
        extracted_dir, _, verified = calls[0]
        assert extracted_dir == target.resolve()
        assert verified is True
        assert "Primed" in stream.getvalue()
    else:
        assert calls == []
        assert "Skipping package cache priming without verified receipt" in (
            stream.getvalue()
        )


@pytest.mark.parametrize("receipt", [False, True], ids=["unsigned", "receipt"])
@pytest.mark.parametrize(
    "target_setup",
    ["non-empty", "file-target", "symlink-target"],
    ids=["non-empty", "file-target", "symlink-target"],
)
def test_execute_unarchive_rejects_existing_target(
    archive_workspace: Path,
    existing_extract_target: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    receipt: bool,
    target_setup: str,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    execute_archive(
        make_args(_ARCHIVE_DEFAULTS, output=archive, receipt=receipt),
        console=console,
    )
    target = existing_extract_target(target_setup)

    with pytest.raises(ArchiveError, match="Cannot extract archive"):
        execute_unarchive(
            make_args(
                _UNARCHIVE_DEFAULTS,
                archive_path=archive,
                target=target,
                receipt=receipt,
            ),
            console=console,
        )

    if target_setup == "non-empty":
        assert (target / "conda.toml").read_text(encoding="utf-8") == "trusted = true\n"
    elif target_setup == "file-target":
        assert target.read_text(encoding="utf-8") == "trusted file\n"


def test_execute_unarchive_require_sha256_requires_receipt(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    execute_archive(make_args(_ARCHIVE_DEFAULTS, output=archive), console=console)

    with pytest.raises(ArchiveError, match="--require-sha256 requires --receipt"):
        execute_unarchive(
            make_args(
                _UNARCHIVE_DEFAULTS,
                archive_path=archive,
                target=tmp_path / "extracted",
                require_sha256=True,
            ),
            console=console,
        )


def test_execute_unarchive_verify_checks_attestation(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (archive_workspace / "conda.lock.sigstore.json").write_text(
        '{"bundle":true}\n',
        encoding="utf-8",
    )
    archive = tmp_path / "signed.tar.gz"
    create_archive(archive_workspace, archive, ArchiveConfig())
    verify_calls: list[dict[str, object]] = []

    def fake_verify_workspace_attestation(**kwargs):
        verify_calls.append(kwargs)

    monkeypatch.setattr(
        "conda_workspaces.attestations.verify_workspace_attestation",
        fake_verify_workspace_attestation,
    )

    stream = StringIO()
    execute_unarchive(
        make_args(
            _UNARCHIVE_DEFAULTS,
            archive_path=archive,
            target=tmp_path / "extracted",
            verify=True,
            cert_identity="user@example.com",
            cert_oidc_issuer="https://issuer.example",
        ),
        console=Console(file=stream, width=200, highlight=False),
    )

    assert len(verify_calls) == 1
    staged_root = verify_calls[0]["root"]
    assert isinstance(staged_root, Path)
    assert staged_root.name.startswith(".extracted.verify-")
    assert verify_calls[0]["manifest_path"] == staged_root / "conda.toml"
    assert verify_calls[0]["lockfile_path"] == staged_root / "conda.lock"
    assert (tmp_path / "extracted" / "conda.lock.sigstore.json").is_file()
    output = stream.getvalue()
    assert "Verified archive" not in output
    assert "conda.lock.sigstore.json attestation" in output


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cert_identity": "user@example.com"},
        {"cert_oidc_issuer": "https://issuer.example"},
    ],
    ids=["identity", "issuer"],
)
def test_execute_unarchive_verification_options_require_verify(
    tmp_path: Path,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(AttestationError, match="require --verify"):
        execute_unarchive(
            make_args(
                _UNARCHIVE_DEFAULTS,
                archive_path=tmp_path / "archive.tar.gz",
                **kwargs,
            ),
            console=Console(file=StringIO(), width=200, highlight=False),
        )


def test_extract_verified_archive_cleans_failed_staging(
    archive_workspace: Path,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "test.tar.gz"
    receipt_path = tmp_path / "test.tar.gz.receipt.json"
    create_console = Console(file=StringIO(), width=200, highlight=False)

    args = make_args(_ARCHIVE_DEFAULTS, output=archive, receipt=receipt_path)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(archive_workspace)
        execute_archive(args, console=create_console)

    receipt = ArchiveReceipt.load(receipt_path)
    receipt.statement["predicate"]["workspace"]["lockfile"] = "../conda.lock"
    target = tmp_path / "target"

    with pytest.raises(ArchiveError, match="relative archive path"):
        extract_verified_archive(archive, target, receipt)

    assert not target.exists()
    assert not list(tmp_path.glob(".target.verify-*"))


def test_execute_unarchive_default_target(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    archive = tmp_path / "my-project.tar.gz"

    monkeypatch.chdir(archive_workspace)
    console = Console(file=StringIO(), width=200, highlight=False)
    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)
    monkeypatch.chdir(tmp_path)

    args_u = make_args(_UNARCHIVE_DEFAULTS, archive_path=archive, target=None)
    result = execute_unarchive(args_u, console=console)

    assert result == 0
    assert (tmp_path / "my-project" / "conda.toml").is_file()


def test_execute_unarchive_no_unsigned_warning(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    target = tmp_path / "extracted"
    args_u = make_args(_UNARCHIVE_DEFAULTS, archive_path=archive, target=target)
    result = execute_unarchive(args_u, console=console)

    assert result == 0
    output = console.file.getvalue()
    lower = output.lower()
    assert "not signed" not in lower
    assert "unsigned" not in lower


def test_execute_unarchive_install_explicit_prefix(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    install_calls: list[object] = []

    def fake_execute_install(args, *, console=None):
        install_calls.append(args)
        return 0

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.execute_install",
        fake_execute_install,
    )

    target = tmp_path / "extracted"
    prefix = "/opt/runtime"
    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=target,
        install=True,
        environment="runtime",
        prefix=prefix,
    )
    result = execute_unarchive(args_u, console=console)

    assert result == 0
    assert len(install_calls) == 1
    install_args = install_calls[0]
    assert install_args.file == str(target)
    assert install_args.environment == "runtime"
    assert install_args.locked is True
    assert install_args.prefix == Path(prefix)
    expected_override = None if str(Path(prefix)) == prefix else prefix
    assert install_args.target_prefix_override == expected_override


@pytest.mark.parametrize(
    ("prefix", "expected_install_suffix"),
    [
        ("/opt/runtime", Path("opt") / "runtime"),
        ("C:/vela/runtime", Path("vela") / "runtime"),
    ],
)
def test_execute_unarchive_install_explicit_prefix_under_dest(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prefix: str,
    expected_install_suffix: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    install_calls: list[object] = []

    def fake_execute_install(args, *, console=None):
        install_calls.append(args)
        return 0

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.execute_install",
        fake_execute_install,
    )

    target = tmp_path / "extracted"
    dest = tmp_path / "rootfs"
    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=target,
        install=True,
        environment="runtime",
        prefix=prefix,
        dest=dest,
    )
    result = execute_unarchive(args_u, console=console)

    assert result == 0
    assert len(install_calls) == 1
    install_args = install_calls[0]
    assert install_args.prefix == dest / expected_install_suffix
    assert install_args.target_prefix_override == prefix


def test_execute_unarchive_install_under_dest_warns_on_staging_prefix_reference(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    stream = StringIO()
    console = Console(file=stream, width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    def fake_execute_install(args, *, console=None):
        script = args.prefix / "bin" / "tool"
        script.parent.mkdir(parents=True)
        script.write_text(f"#!{args.prefix}/bin/python\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.execute_install",
        fake_execute_install,
    )

    target = tmp_path / "extracted"
    dest = tmp_path / "rootfs"
    prefix = "/opt/runtime"
    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=target,
        install=True,
        environment="runtime",
        prefix=prefix,
        dest=dest,
    )
    result = execute_unarchive(args_u, console=console)

    assert result == 0
    output = stream.getvalue()
    assert "Warning:" in output
    assert "installed files still reference the staging prefix" in output
    assert str(dest / "opt" / "runtime") in output
    assert "/opt/runtime" in output
    assert "bin/tool" in output.replace("\\", "/")


def test_execute_unarchive_install_under_dest_without_staging_prefix_reference(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    stream = StringIO()
    console = Console(file=stream, width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    def fake_execute_install(args, *, console=None):
        script = args.prefix / "bin" / "tool"
        script.parent.mkdir(parents=True)
        script.write_text(
            f"#!{args.target_prefix_override}/bin/python\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.execute_install",
        fake_execute_install,
    )

    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=tmp_path / "extracted",
        install=True,
        environment="runtime",
        prefix="/opt/runtime",
        dest=tmp_path / "rootfs",
    )
    result = execute_unarchive(args_u, console=console)

    assert result == 0
    assert "Warning:" not in stream.getvalue()


def test_execute_unarchive_prefix_requires_install(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=tmp_path / "extracted",
        environment="runtime",
        prefix="/opt/runtime",
    )
    with pytest.raises(ArchiveError, match="--prefix requires --install"):
        execute_unarchive(args_u, console=console)


def test_execute_unarchive_prefix_requires_environment(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=tmp_path / "extracted",
        install=True,
        prefix="/opt/runtime",
    )
    with pytest.raises(ArchiveError, match="--prefix requires an explicit"):
        execute_unarchive(args_u, console=console)


def test_execute_unarchive_dest_requires_prefix(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=tmp_path / "extracted",
        install=True,
        environment="runtime",
        dest=tmp_path / "rootfs",
    )
    with pytest.raises(ArchiveError, match="--dest requires --prefix"):
        execute_unarchive(args_u, console=console)


def test_execute_unarchive_prefix_must_be_absolute(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=tmp_path / "extracted",
        install=True,
        environment="runtime",
        prefix=Path("relative/prefix"),
    )
    with pytest.raises(ArchiveError, match="--prefix must be an absolute path"):
        execute_unarchive(args_u, console=console)
