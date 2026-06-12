"""Tests for conda workspace archive and unarchive."""

from __future__ import annotations

import json
import tarfile
from io import StringIO
from pathlib import Path, PureWindowsPath

import pytest
from rich.console import Console

from conda_workspaces.cli.workspace.archive import (
    execute_archive,
    execute_unarchive,
    extract_verified_archive,
    file_contains_bytes,
    is_absolute_runtime_prefix,
    receipt_environment_prefixes,
    resolve_receipt_path,
    runtime_prefix_relative_path,
    scan_prefix_references,
)
from conda_workspaces.exceptions import ArchiveError
from conda_workspaces.receipts import ArchiveReceipt

from ..conftest import make_args

_ARCHIVE_DEFAULTS = {
    "file": None,
    "output": None,
    "bundle": False,
    "lock": False,
    "exclude": None,
    "receipt": None,
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
    "target_setup",
    ["non-empty", "file-target", "symlink-target"],
    ids=["non-empty", "file-target", "symlink-target"],
)
def test_execute_unarchive_receipt_rejects_existing_target(
    archive_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_setup: str,
) -> None:
    monkeypatch.chdir(archive_workspace)
    archive = tmp_path / "test.tar.gz"
    console = Console(file=StringIO(), width=200, highlight=False)

    execute_archive(
        make_args(_ARCHIVE_DEFAULTS, output=archive, receipt=True),
        console=console,
    )
    target = tmp_path / "extracted"
    if target_setup == "non-empty":
        target.mkdir()
        (target / "existing.txt").write_text("x", encoding="utf-8")
    elif target_setup == "file-target":
        target.write_text("x", encoding="utf-8")
    else:
        target.symlink_to(tmp_path)

    with pytest.raises(ArchiveError, match="Cannot verify receipt"):
        execute_unarchive(
            make_args(
                _UNARCHIVE_DEFAULTS,
                archive_path=archive,
                target=target,
                receipt=True,
            ),
            console=console,
        )


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
