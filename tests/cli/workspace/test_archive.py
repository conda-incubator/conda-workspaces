"""Tests for conda workspace archive and unarchive."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from io import StringIO
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

import pytest
from conda.base.context import context
from rich.console import Console

from conda_workspaces.archive import (
    create_archive,
    file_contains_bytes,
    is_absolute_runtime_prefix,
    receipt_environment_prefixes,
    resolve_receipt_path,
    runtime_prefix_relative_path,
    scan_prefix_references,
)
from conda_workspaces.cli.workspace.archive import (
    execute_archive,
    execute_unarchive,
    warn_staging_prefix_references,
)
from conda_workspaces.exceptions import ArchiveError
from conda_workspaces.models import ArchiveConfig
from conda_workspaces.receipts import ArchiveReceipt

from ..conftest import make_args

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import SnapshotTree

_ARCHIVE_DEFAULTS = {
    "manifest_file": None,
    "output": None,
    "bundle": False,
    "lock": False,
    "exclude": None,
    "receipt": None,
    "dry_run": False,
    "json": False,
}

_UNARCHIVE_DEFAULTS = {
    "manifest_file": None,
    "archive_path": None,
    "target": None,
    "install": False,
    "no_install": False,
    "environment": None,
    "prefix": None,
    "dest": None,
    "receipt": None,
    "require_sha256": False,
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


@pytest.mark.parametrize("prefix", ["/../escape", r"C:\..\escape"])
def test_runtime_prefix_relative_path_rejects_traversal(prefix: str) -> None:
    with pytest.raises(ValueError, match="contains traversal"):
        runtime_prefix_relative_path(prefix)


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


@pytest.fixture
def archive_workspace(tmp_path: Path) -> Path:
    platform = context.subdir
    manifest = f"""\
[workspace]
name = "archive-test"
channels = ["conda-forge"]
platforms = ["{platform}"]
"""
    (tmp_path / "conda.toml").write_text(manifest, encoding="utf-8")
    (tmp_path / "conda.lock").write_text(
        "version: 1\nenvironments:\n  default:\n    channels:\n"
        "      - url: https://conda.anaconda.org/conda-forge/\n"
        f"    packages:\n      {platform}: []\npackages: []\n",
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


def test_execute_archive_uses_exact_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (
        '[workspace]\nname = "{name}"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n'
    )
    (tmp_path / "conda.toml").write_text(
        manifest.format(name="conda-priority"),
        encoding="utf-8",
    )
    pixi = tmp_path / "pixi.toml"
    pixi.write_text(
        manifest.format(name="pixi-selected"),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    execute_archive(
        make_args(_ARCHIVE_DEFAULTS, manifest_file=pixi),
        console=Console(file=StringIO(), width=200, highlight=False),
    )

    assert (tmp_path / "pixi-selected.tar.zst").is_file()
    assert not (tmp_path / "conda-priority.tar.zst").exists()


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
    "existing_lock",
    [False, True],
    ids=["prospective-lock", "existing-lock"],
)
def test_execute_archive_dry_run_preserves_outputs(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    existing_lock: bool,
) -> None:
    monkeypatch.chdir(archive_workspace)
    if not existing_lock:
        (archive_workspace / "conda.lock").unlink()
        subprocess.run(
            ["git", "init"],
            cwd=archive_workspace,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "conda.toml", "src/app.py"],
            cwd=archive_workspace,
            check=True,
            capture_output=True,
        )
    output = archive_workspace / "preview.tar.gz"
    receipt = archive_workspace / "preview.receipt.json"
    output.write_bytes(b"existing archive")
    receipt.write_bytes(b"existing receipt")
    calls: list[tuple[list[str], bool]] = []

    monkeypatch.setattr(
        "conda_workspaces.resolver.resolve_all_environments",
        lambda config, platform: {"default": object()},
    )

    def fake_render(ctx, resolved_envs, **kwargs):
        calls.append((list(resolved_envs), kwargs["dry_run"]))
        return (
            "version: 1\nenvironments:\n  default:\n"
            "    channels: []\n    packages:\n      linux-64: []\n"
            "packages: []\n"
        )

    monkeypatch.setattr(
        "conda_workspaces.lockfile.render_lockfile",
        fake_render,
    )
    before = snapshot_tree(archive_workspace)
    stream = StringIO()

    result = execute_archive(
        make_args(
            _ARCHIVE_DEFAULTS,
            output=output,
            receipt=receipt,
            lock=True,
            dry_run=True,
        ),
        console=Console(file=stream, width=200, highlight=False),
    )

    assert result == 0
    assert calls == [(["default"], True)]
    assert snapshot_tree(archive_workspace) == before
    assert "Would create" in stream.getvalue()


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


@pytest.mark.parametrize("dry_run", [False, True], ids=["extract", "dry-run"])
def test_execute_unarchive_receipt_detects_tampered_archive(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dry_run: bool,
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
                dry_run=dry_run,
            ),
            console=console,
        )


@pytest.mark.parametrize("receipt", [False, True], ids=["unsigned", "receipt"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["extract", "dry-run"])
@pytest.mark.parametrize(
    "target_setup",
    ["empty", "non-empty", "file-target", "symlink-target"],
    ids=["empty", "non-empty", "file-target", "symlink-target"],
)
def test_execute_unarchive_rejects_existing_target(
    archive_workspace: Path,
    existing_extract_target: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    receipt: bool,
    dry_run: bool,
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
                dry_run=dry_run,
            ),
            console=console,
        )

    if target_setup == "non-empty":
        assert (target / "conda.toml").read_text(encoding="utf-8") == "trusted = true\n"
    elif target_setup == "file-target":
        assert target.read_text(encoding="utf-8") == "trusted file\n"


@pytest.mark.parametrize(
    ("already_cached", "expected_count"),
    [(False, 1), (True, 1)],
    ids=["uncached", "archive-only"],
)
def test_execute_unarchive_dry_run_reports_prospective_cache_count(
    bundled_cli_archive: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    already_cached: bool,
    expected_count: int,
) -> None:
    from conda.base.context import context as conda_context

    cache = tmp_path / "preview-cache"
    cache.mkdir()
    if already_cached:
        (cache / "example-1.0-h123.conda").write_bytes(b"example package")
    monkeypatch.setattr(
        type(conda_context),
        "pkgs_dirs",
        property(lambda self: (str(cache),)),
    )
    stream = StringIO()

    result = execute_unarchive(
        make_args(
            _UNARCHIVE_DEFAULTS,
            archive_path=bundled_cli_archive,
            target=tmp_path / "target",
            receipt=True,
            dry_run=True,
        ),
        console=Console(file=stream, width=200, highlight=False),
    )

    assert result == 0
    assert f"Would prime {expected_count} packages" in stream.getvalue()
    assert not (tmp_path / "target").exists()


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


@pytest.mark.parametrize("install", [False, True], ids=["extract", "install"])
def test_execute_unarchive_dry_run_preserves_tree(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot_tree: SnapshotTree,
    install: bool,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "dry-run.tar.gz"
    execute_archive(
        make_args(
            _ARCHIVE_DEFAULTS,
            output=archive,
            receipt=True,
        ),
        console=Console(file=StringIO(), width=200, highlight=False),
    )
    target = tmp_path / "extracted"
    dest = tmp_path / "staging"
    before = snapshot_tree(tmp_path)
    stream = StringIO()

    result = execute_unarchive(
        make_args(
            _UNARCHIVE_DEFAULTS,
            archive_path=archive,
            target=target,
            receipt=True,
            install=install,
            environment="default" if install else None,
            prefix="/opt/runtime" if install else None,
            dest=dest if install else None,
            dry_run=True,
        ),
        console=Console(file=stream, width=200, highlight=False),
    )

    assert result == 0
    assert snapshot_tree(tmp_path) == before
    assert not target.exists()
    assert not dest.exists()
    assert "Would extract" in stream.getvalue()
    if install:
        assert "Would install" in stream.getvalue()


def test_execute_unarchive_dry_run_resolves_relative_target(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "workspace.tar.gz"
    create_archive(archive_workspace, archive, ArchiveConfig())
    monkeypatch.chdir(tmp_path)
    stream = StringIO()

    assert (
        execute_unarchive(
            make_args(
                _UNARCHIVE_DEFAULTS,
                archive_path=archive,
                target=Path("relative-target"),
                dry_run=True,
            ),
            console=Console(file=stream, width=200, highlight=False),
        )
        == 0
    )

    target = tmp_path / "relative-target"
    assert str(target.resolve()) in stream.getvalue()
    assert not target.exists()


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
    replace_lockfile_install_plan,
) -> None:
    platform = context.subdir
    (archive_workspace / "conda.toml").write_text(
        f"""\
[workspace]
name = "archive-test"
channels = ["conda-forge"]
platforms = ["{platform}"]
""",
        encoding="utf-8",
    )
    (archive_workspace / "conda.lock").write_text(
        "version: 1\nenvironments:\n  default:\n    channels:\n"
        "      - url: https://conda.anaconda.org/conda-forge/\n"
        f"    packages:\n      {platform}: []\npackages: []\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    args_a = make_args(_ARCHIVE_DEFAULTS, output=archive)
    execute_archive(args_a, console=console)

    install_calls: list[tuple[str, object, str, dict[str, object]]] = []

    def fake_install_from_lockfile(phase, ctx, name, kwargs):
        install_calls.append((phase, ctx, name, kwargs))

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        fake_install_from_lockfile,
    )

    target = tmp_path / "extracted"
    prefix = str(tmp_path / "runtime")
    args_u = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=target,
        install=True,
        environment="default",
        prefix=prefix,
    )
    result = execute_unarchive(args_u, console=console)

    assert result == 0
    assert len(install_calls) == 2
    preview_phase, preview_ctx, preview_environment, preview_kwargs = install_calls[0]
    install_phase, install_ctx, environment, install_kwargs = install_calls[1]
    assert preview_ctx is install_ctx
    assert preview_environment == environment == "default"
    assert preview_phase == "prepare"
    assert install_phase == "execute"
    assert preview_kwargs["lockfile_data"] is install_kwargs["lockfile_data"]
    assert install_ctx.root == target
    assert Path(install_ctx.config.manifest_path) == target / "conda.toml"
    expected_override = None if str(Path(prefix)) == prefix else prefix
    lockfile_data = install_kwargs.pop("lockfile_data")
    assert lockfile_data["version"] == 1
    assert lockfile_data["environments"]["default"]["packages"][platform] == []
    validate_workspace = install_kwargs.pop("validate_workspace")
    assert callable(validate_workspace)
    validate_workspace()
    assert install_kwargs == {
        "prefix": Path(prefix),
        "replace_existing": False,
        "target_prefix_override": expected_override,
    }


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
        environment="default",
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
        environment="default",
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


def test_staging_prefix_warning_escapes_terminal_controls(tmp_path: Path) -> None:
    install_prefix = tmp_path / "prefix\x1b]52;c;PREFIX\x07"
    matched = install_prefix / "bad\x1b]52;c;MATCH\x07.txt"
    stream = StringIO()

    warn_staging_prefix_references(
        Console(file=stream, width=200, highlight=False),
        install_prefix=install_prefix,
        runtime_prefix="/opt/runtime\x1b]52;c;RUNTIME\x07",
        matches=(matched,),
    )

    output = stream.getvalue()
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "]52;c;PREFIX" in output
    assert "]52;c;MATCH" in output
    assert "]52;c;RUNTIME" in output
    assert r"\x1b" in output
    assert r"\x07" in output


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
        environment="default",
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


@pytest.mark.skipif(os.name == "nt", reason="Windows syntax is host-native on Windows")
def test_execute_unarchive_prefix_must_use_host_syntax_without_dest(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)
    execute_archive(make_args(_ARCHIVE_DEFAULTS, output=archive), console=console)

    args = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=tmp_path / "extracted",
        install=True,
        environment="runtime",
        prefix=r"C:\vela\runtime",
    )
    with pytest.raises(ArchiveError, match="host platform's absolute path syntax"):
        execute_unarchive(args, console=console)


@pytest.mark.parametrize("prefix", ["/../escape", r"C:\..\escape"])
def test_execute_unarchive_dest_rejects_prefix_traversal(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prefix: str,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)
    execute_archive(make_args(_ARCHIVE_DEFAULTS, output=archive), console=console)

    dest = tmp_path / "rootfs"
    args = make_args(
        _UNARCHIVE_DEFAULTS,
        archive_path=archive,
        target=tmp_path / "extracted",
        install=True,
        environment="runtime",
        prefix=prefix,
        dest=dest,
    )
    with pytest.raises(ArchiveError, match="must not contain"):
        execute_unarchive(args, console=console)

    assert not dest.exists()
