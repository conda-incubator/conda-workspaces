"""Tests for conda_workspaces.paths."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path, PurePosixPath

import pytest

from conda_workspaces import paths as paths_mod
from conda_workspaces.paths import (
    atomic_binary_writer,
    atomic_write_text,
    has_absolute_path_syntax,
    is_path_segment,
    parse_relative_posix_path,
    portable_path_key,
    read_regular_file_bytes,
    regular_file_generation,
    resolve_relative_path,
    supports_anchored_directory_operations,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/tmp/project", True),
        ("C:/project", True),
        (r"C:\project", True),
        ("relative/project", False),
        (r"relative\project", False),
        ("C:project", False),
    ],
    ids=[
        "posix-absolute",
        "windows-absolute-forward",
        "windows-absolute-backslash",
        "relative-posix",
        "relative-windows",
        "windows-drive-relative",
    ],
)
def test_has_absolute_path_syntax(value: str, expected: bool) -> None:
    assert has_absolute_path_syntax(value) is expected


def test_portable_path_key_normalizes_unicode() -> None:
    assert portable_path_key(
        PurePosixPath("caf\N{LATIN SMALL LETTER E WITH ACUTE}")
    ) == portable_path_key(
        PurePosixPath("caf\N{LATIN SMALL LETTER E}\N{COMBINING ACUTE ACCENT}")
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows stat semantics")
@pytest.mark.parametrize("follow_symlinks", [True, False], ids=["stat", "lstat"])
def test_stat_generation_matches_windows_file_descriptor(
    tmp_path: Path,
    follow_symlinks: bool,
) -> None:
    path = tmp_path / "input.txt"
    path.write_text("content", encoding="utf-8")
    path_stat = path.stat() if follow_symlinks else path.lstat()

    with path.open("rb") as stream:
        descriptor_stat = os.fstat(stream.fileno())

    assert paths_mod._stat_generation(path_stat) == paths_mod._stat_generation(
        descriptor_stat
    )
    assert paths_mod._stat_generation(path_stat)[-1] == getattr(
        path_stat,
        "st_birthtime_ns",
        path_stat.st_ctime_ns,
    )


@pytest.mark.parametrize(
    ("value", "allow_parent", "require_canonical", "expected"),
    [
        ("environment.yml", False, False, "environment.yml"),
        ("envs/default.yml", False, False, "envs/default.yml"),
        ("./envs/default.yml", False, False, "envs/default.yml"),
        ("envs//default.yml", False, False, "envs/default.yml"),
        ("envs/./default.yml", False, False, "envs/default.yml"),
        ("../target", True, False, "../target"),
    ],
    ids=[
        "file",
        "nested",
        "current-dir-prefix",
        "double-separator",
        "current-dir-segment",
        "allowed-parent",
    ],
)
def test_parse_relative_posix_path_accepts_valid_paths(
    value: str,
    allow_parent: bool,
    require_canonical: bool,
    expected: str,
) -> None:
    path = parse_relative_posix_path(
        value,
        allow_parent=allow_parent,
        require_canonical=require_canonical,
    )

    assert path.as_posix() == expected


@pytest.mark.parametrize(
    ("value", "allow_parent", "require_canonical"),
    [
        ("", False, False),
        ("/tmp/project", False, False),
        ("C:/project", False, False),
        (r"C:\project", False, False),
        (r"dir\file", False, False),
        (r"\file", False, False),
        (r"\\server\share\file", False, False),
        ("C:project", True, False),
        ("../file", False, False),
        ("dir/../file", False, False),
        ("./envs/default.yml", False, True),
        ("envs//default.yml", False, True),
        ("envs/./default.yml", False, True),
        ("bad\0path", False, False),
        ("bad\x7fpath", False, False),
        ("bad\x85path", False, False),
        ("CON", False, False),
        ("dir/nul.txt", False, False),
        ("name.", False, False),
        ("name ", False, False),
        ("name:stream", False, False),
        ("bad<name", False, False),
        ("bad>name", False, False),
        ('bad"name', False, False),
        ("bad|name", False, False),
        ("bad?name", False, False),
        ("bad*name", False, False),
        ("a" * 256, False, False),
    ],
    ids=[
        "empty",
        "posix-absolute",
        "windows-absolute-forward",
        "windows-absolute-backslash",
        "backslash",
        "windows-rooted",
        "windows-unc",
        "windows-drive-relative",
        "parent",
        "nested-parent",
        "current-dir-canonical",
        "double-slash-canonical",
        "current-dir-segment-canonical",
        "nul",
        "del-control",
        "c1-control",
        "windows-device",
        "nested-windows-device",
        "windows-trailing-dot",
        "windows-trailing-space",
        "windows-alternate-data-stream",
        "windows-less-than",
        "windows-greater-than",
        "windows-quote",
        "windows-pipe",
        "windows-question-mark",
        "windows-asterisk",
        "component-too-long",
    ],
)
def test_parse_relative_posix_path_rejects_invalid_paths(
    value: str,
    allow_parent: bool,
    require_canonical: bool,
) -> None:
    with pytest.raises(ValueError):
        parse_relative_posix_path(
            value,
            allow_parent=allow_parent,
            require_canonical=require_canonical,
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("archive-test", True),
        ("archive test", True),
        ("", False),
        (".", False),
        ("..", False),
        ("nested/archive-test", False),
        (r"nested\archive-test", False),
        ("/tmp/archive-test", False),
        (r"\archive-test", False),
        ("C:archive-test", False),
        ("C:/archive-test", False),
        (r"C:\archive-test", False),
        (r"\\server\share\archive-test", False),
        ("bad\0name", False),
        ("bad\x7fname", False),
        ("bad\x85name", False),
        ("COM1.log", False),
        ("archive-test.", False),
        ("archive-test ", False),
        ("archive-test:stream", False),
        ("bad<name", False),
        ("bad>name", False),
        ('bad"name', False),
        ("bad|name", False),
        ("bad?name", False),
        ("bad*name", False),
        ("a" * 256, False),
    ],
    ids=[
        "name",
        "space",
        "empty",
        "dot",
        "dot-dot",
        "nested-posix",
        "nested-windows",
        "absolute",
        "windows-rooted",
        "windows-drive-relative",
        "windows-absolute-forward",
        "windows-absolute-backslash",
        "windows-unc",
        "nul",
        "del-control",
        "c1-control",
        "windows-device-extension",
        "windows-trailing-dot",
        "windows-trailing-space",
        "windows-alternate-data-stream",
        "windows-less-than",
        "windows-greater-than",
        "windows-quote",
        "windows-pipe",
        "windows-question-mark",
        "windows-asterisk",
        "component-too-long",
    ],
)
def test_is_path_segment(value: str, expected: bool) -> None:
    assert is_path_segment(value) is expected


@pytest.mark.parametrize(
    "relative_path",
    [
        PurePosixPath("environment.yml"),
        PurePosixPath("envs/default.yml"),
    ],
    ids=["file", "nested"],
)
def test_resolve_relative_path_returns_paths_inside_root(
    tmp_path: Path,
    relative_path: PurePosixPath,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    assert resolve_relative_path(root, relative_path) == root.joinpath(
        *relative_path.parts
    ).resolve(strict=False)


def test_resolve_relative_path_rejects_symlink_escape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    try:
        (project / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(ValueError):
        resolve_relative_path(project, PurePosixPath("linked/environment.yml"))


@pytest.mark.parametrize("target_exists", [True, False], ids=["regular", "broken"])
def test_atomic_write_text_rejects_symlink_target(
    tmp_path: Path,
    target_exists: bool,
) -> None:
    target = (
        tmp_path / "target.txt"
        if target_exists
        else tmp_path / "missing" / "target.txt"
    )
    if target_exists:
        target.write_text("old", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(ValueError, match="symbolic link"):
        atomic_write_text(link, "new")

    assert link.is_symlink()
    assert target.exists() is target_exists
    if target_exists:
        assert link.read_text(encoding="utf-8") == "old"
        assert target.read_text(encoding="utf-8") == "old"
    expected_names = {"link.txt", "target.txt"} if target_exists else {"link.txt"}
    assert {path.name for path in tmp_path.iterdir()} == expected_names


def test_atomic_write_text_rejects_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(NotADirectoryError, match="symbolic link"):
        atomic_write_text(linked_parent / "nested" / "output.txt", "new")

    assert not (outside / "nested").exists()


def test_atomic_write_text_does_not_follow_raced_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")

    project = tmp_path / "project"
    parent = project / "parent"
    displaced = project / "displaced"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    original_mkdir = paths_mod.os.mkdir
    replaced = False

    def replace_parent(path, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
        nonlocal replaced
        if path == "nested" and dir_fd is not None and not replaced:
            parent.rename(displaced)
            parent.symlink_to(outside, target_is_directory=True)
            replaced = True
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(paths_mod.os, "mkdir", replace_parent)

    with pytest.raises(NotADirectoryError, match="changed while it was opened"):
        atomic_write_text(parent / "nested" / "output.txt", "new")

    assert replaced is True
    assert not (outside / "nested").exists()
    assert not (displaced / "nested" / "output.txt").exists()


def test_atomic_write_text_does_not_overwrite_raced_new_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    path = tmp_path / "output.txt"
    original_rename = paths_mod.rename_noreplace
    raced = False

    def create_competitor(*args, **kwargs):
        nonlocal raced
        if not raced:
            path.write_text("competitor", encoding="utf-8")
            raced = True
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(paths_mod, "rename_noreplace", create_competitor)

    with pytest.raises(FileExistsError):
        atomic_write_text(path, "new")

    assert raced is True
    assert path.read_text(encoding="utf-8") == "competitor"
    assert not list(tmp_path.glob(".output.txt.*.tmp"))


def test_atomic_write_text_retains_raced_existing_target_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not supports_anchored_directory_operations() or sys.platform not in {
        "darwin",
        "linux",
    }:
        pytest.skip("atomic name exchange is unavailable")
    path = tmp_path / "output.txt"
    displaced = tmp_path / "displaced.txt"
    path.write_text("old", encoding="utf-8")
    original_stat = paths_mod.os.stat
    target_stats = 0
    raced = False

    def replace_after_current_check(
        target,
        *,
        dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal raced, target_stats
        result = original_stat(
            target,
            dir_fd=dir_fd,
            follow_symlinks=follow_symlinks,
        )
        if target == path.name and dir_fd is not None:
            target_stats += 1
        if target_stats == 2 and not raced:
            path.rename(displaced)
            path.write_text("competitor", encoding="utf-8")
            raced = True
        return result

    monkeypatch.setattr(paths_mod.os, "stat", replace_after_current_check)

    with pytest.raises(ValueError, match="changed during publication"):
        atomic_write_text(path, "new")

    assert raced is True
    assert path.read_text(encoding="utf-8") == "new"
    assert displaced.read_text(encoding="utf-8") == "old"
    recovery = list(tmp_path.glob(".output.txt.*.tmp"))
    assert len(recovery) == 1
    assert recovery[0].read_text(encoding="utf-8") == "competitor"


@pytest.mark.parametrize("mutation", ["replace", "rewrite"])
def test_atomic_binary_writer_rejects_generation_change_while_writing(
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / "output.txt"
    path.write_text("original", encoding="utf-8")
    generation = regular_file_generation(path)
    concurrent_content = "concurrent generation"

    with pytest.raises(ValueError, match="changed while writing"):
        with atomic_binary_writer(
            path,
            expected_generation=generation,
        ) as stream:
            stream.write(b"replacement")
            if mutation == "replace":
                replacement = path.with_name("replacement.txt")
                replacement.write_text(concurrent_content, encoding="utf-8")
                replacement.replace(path)
            else:
                path.write_text(concurrent_content, encoding="utf-8")

    assert path.read_text(encoding="utf-8") == concurrent_content


def test_atomic_write_text_drops_special_permission_bits(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX permission bits are unavailable")
    path = tmp_path / "output.txt"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o6755)

    atomic_write_text(path, "new")

    assert stat.S_IMODE(path.stat().st_mode) == 0o755


def test_read_regular_file_bytes_rejects_same_length_in_place_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "input.txt"
    path.write_bytes(b"AAAA")
    original_lstat = Path.lstat
    replaced = False

    def rewrite_after_stat(self: Path):
        nonlocal replaced
        result = original_lstat(self)
        if self == path and not replaced:
            replaced = True
            path.write_bytes(b"BBBB")
            os.utime(
                path,
                ns=(result.st_atime_ns, result.st_mtime_ns + 1_000_000_000),
            )
        return result

    monkeypatch.setattr(Path, "lstat", rewrite_after_stat)

    with pytest.raises(ValueError, match="changed while it was read"):
        read_regular_file_bytes(path, maximum_bytes=4, label="test input")

    assert replaced is True


def test_read_regular_file_bytes_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable")
    path = tmp_path / "input.pipe"
    os.mkfifo(path)

    with pytest.raises(ValueError, match="Cannot read test input safely"):
        read_regular_file_bytes(path, maximum_bytes=4, label="test input")
