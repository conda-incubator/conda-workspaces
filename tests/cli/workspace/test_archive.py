"""Tests for conda workspace archive and unarchive."""

from __future__ import annotations

import tarfile
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from conda_workspaces.cli.workspace.archive import execute_archive, execute_unarchive
from conda_workspaces.exceptions import ArchiveError

from ..conftest import make_args

_ARCHIVE_DEFAULTS = {
    "file": None,
    "output": None,
    "bundle": False,
    "lock": False,
    "exclude": None,
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
    "dry_run": False,
    "json": False,
}


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
    prefix = Path("/opt/runtime")
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
    assert install_args.prefix == prefix
    assert install_args.target_prefix_override is None


def test_execute_unarchive_install_explicit_prefix_under_dest(
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
    dest = tmp_path / "rootfs"
    prefix = Path("/opt/runtime")
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
    assert install_args.prefix == dest / "opt" / "runtime"
    assert install_args.target_prefix_override == prefix


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
        prefix=Path("/opt/runtime"),
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
        prefix=Path("/opt/runtime"),
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
