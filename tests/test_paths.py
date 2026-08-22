"""Tests for conda_workspaces.paths."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

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
def test_file_generation_matches_windows_file_descriptor(
    tmp_path: Path,
    follow_symlinks: bool,
) -> None:
    path = tmp_path / "input.txt"
    path.write_text("content", encoding="utf-8")
    path_stat = path.stat() if follow_symlinks else path.lstat()

    with path.open("rb") as stream:
        descriptor_stat = os.fstat(stream.fileno())

    assert paths_mod.file_generation(path_stat) == paths_mod.file_generation(
        descriptor_stat
    )
    assert paths_mod.file_generation(path_stat)[-1] == getattr(
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


@pytest.mark.skipif(sys.platform != "linux", reason="Linux renameat2 syscall")
@pytest.mark.parametrize(
    ("flag", "exchange"),
    [(1, False), (2, True)],
    ids=["noreplace", "exchange"],
)
def test_linux_flagged_rename_uses_syscall_without_libc_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: int,
    exchange: bool,
) -> None:
    assert paths_mod._LIBC is not None

    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    if exchange:
        destination.write_text("destination", encoding="utf-8")
    monkeypatch.setattr(
        paths_mod,
        "_LIBC",
        SimpleNamespace(syscall=paths_mod._LIBC.syscall),
    )

    paths_mod._rename_with_flags(source, destination, flag=flag)

    assert destination.read_text(encoding="utf-8") == "source"
    if exchange:
        assert source.read_text(encoding="utf-8") == "destination"
    else:
        assert not source.exists()


@pytest.mark.parametrize(
    "error_number",
    [errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP],
    ids=["filesystem", "kernel", "operation"],
)
@pytest.mark.parametrize(
    ("flag", "message"),
    [
        (1, "exclusive rename"),
        (2, "atomic name exchange"),
    ],
    ids=["noreplace", "exchange"],
)
def test_linux_flagged_rename_reports_unavailable_support(
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    flag: int,
    message: str,
) -> None:
    monkeypatch.setattr(paths_mod, "_LIBC", SimpleNamespace(syscall=lambda *args: -1))
    monkeypatch.setattr(paths_mod.ctypes, "get_errno", lambda: error_number)
    monkeypatch.setattr(paths_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        paths_mod.os,
        "uname",
        lambda: SimpleNamespace(machine="x86_64"),
        raising=False,
    )

    with pytest.raises(
        NotImplementedError,
        match=rf"{message} is unavailable for these paths",
    ) as exc_info:
        paths_mod._rename_with_flags("source", "destination", flag=flag)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert exc_info.value.__cause__.errno == error_number


@pytest.mark.parametrize("existing", [False, True], ids=["new", "replace"])
def test_atomic_write_text_translates_unavailable_flagged_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
) -> None:
    if not supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    path = tmp_path / "output.txt"
    if existing:
        path.write_text("original", encoding="utf-8")

    def unavailable(*args, **kwargs) -> None:
        raise NotImplementedError("flagged rename unavailable")

    monkeypatch.setattr(paths_mod, "_rename_with_flags", unavailable)

    with pytest.raises(ValueError, match="Safe publication is unavailable"):
        atomic_write_text(path, "new")

    if existing:
        assert path.read_text(encoding="utf-8") == "original"
    else:
        assert not path.exists()
    assert not list(tmp_path.glob(f".{path.name}.*.tmp"))


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


def test_atomic_write_text_restores_raced_existing_target(
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
    original_exchange = paths_mod._rename_with_flags
    raced = False

    def replace_before_exchange(
        source,
        destination,
        *,
        flag,
        source_dir_fd=None,
        destination_dir_fd=None,
    ) -> None:
        nonlocal raced
        if flag == 2 and Path(destination).name == path.name and not raced:
            path.rename(displaced)
            path.write_text("competitor", encoding="utf-8")
            raced = True
        original_exchange(
            source,
            destination,
            flag=flag,
            source_dir_fd=source_dir_fd,
            destination_dir_fd=destination_dir_fd,
        )

    monkeypatch.setattr(paths_mod, "_rename_with_flags", replace_before_exchange)

    with pytest.raises(ValueError, match="changed during publication"):
        atomic_write_text(path, "new")

    assert raced is True
    assert path.read_text(encoding="utf-8") == "competitor"
    assert displaced.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".output.txt.*.tmp"))


@pytest.mark.parametrize(
    "concurrent_claimant",
    [None, "replace", "rewrite"],
    ids=["restore", "replacement-recovery", "rewrite-recovery"],
)
def test_atomic_write_text_restores_same_inode_rewrite_after_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    concurrent_claimant: str | None,
) -> None:
    if not supports_anchored_directory_operations() or sys.platform not in {
        "darwin",
        "linux",
    }:
        pytest.skip("atomic name exchange is unavailable")
    path = tmp_path / "output.txt"
    path.write_bytes(b"AAAA")
    initial = path.stat()
    original_exchange = paths_mod._rename_with_flags
    exchanges = 0

    def race_exchange(
        source,
        destination,
        *,
        flag,
        source_dir_fd=None,
        destination_dir_fd=None,
    ) -> None:
        nonlocal exchanges
        if flag == 2 and Path(destination).name == path.name:
            exchanges += 1
            if exchanges == 1:
                path.write_bytes(b"BBBB")
                os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            elif exchanges == 2:
                if concurrent_claimant == "replace":
                    claimant = tmp_path / "claimant.txt"
                    claimant.write_bytes(b"claimant")
                    claimant.replace(path)
                elif concurrent_claimant == "rewrite":
                    published = path.stat()
                    path.write_bytes(b"bad")
                    os.utime(
                        path,
                        ns=(published.st_atime_ns, published.st_mtime_ns),
                    )
        original_exchange(
            source,
            destination,
            flag=flag,
            source_dir_fd=source_dir_fd,
            destination_dir_fd=destination_dir_fd,
        )

    monkeypatch.setattr(paths_mod, "_rename_with_flags", race_exchange)

    with pytest.raises(ValueError, match="changed during publication"):
        atomic_write_text(path, "new")

    assert exchanges == 2
    assert path.read_bytes() == b"BBBB"
    recoveries = list(tmp_path.glob(".output.txt.*.tmp"))
    if concurrent_claimant is not None:
        assert len(recoveries) == 1
        expected_recovery = b"claimant" if concurrent_claimant == "replace" else b"bad"
        assert recoveries[0].read_bytes() == expected_recovery
    else:
        assert not recoveries


@pytest.mark.parametrize(
    "race",
    [None, "publish-error", "competitor", "same-inode-rewrite"],
    ids=["success", "publish-error", "competitor", "same-inode-rewrite"],
)
def test_atomic_write_text_fallback_preserves_existing_output_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str | None,
) -> None:
    if os.name != "nt" and sys.platform not in {"darwin", "linux"}:
        pytest.skip("safe exclusive rename is unavailable")
    monkeypatch.setattr(paths_mod, "_SUPPORTS_ANCHORED_DIRECTORY_OPERATIONS", False)
    path = tmp_path / "output.txt"
    original_content = b"AAAA"
    path.write_bytes(original_content)
    initial = path.stat()
    original_rename = paths_mod.rename_noreplace
    raced = False

    def race_publication(source, destination, **kwargs) -> None:
        nonlocal raced
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            race == "same-inode-rewrite"
            and source_path == path
            and destination_path.name.endswith(".rollback")
            and not raced
        ):
            path.write_bytes(b"BBBB")
            os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            raced = True
        elif (
            race in {"publish-error", "competitor"}
            and source_path.name.startswith(f".{path.name}.")
            and source_path.name.endswith(".tmp")
            and destination_path == path
            and not raced
        ):
            raced = True
            if race == "publish-error":
                raise OSError("injected publication error")
            path.write_bytes(b"competitor")
        original_rename(source, destination, **kwargs)

    monkeypatch.setattr(paths_mod, "rename_noreplace", race_publication)

    if race is None:
        atomic_write_text(path, "new")
    else:
        with pytest.raises(ValueError, match="changed during publication"):
            atomic_write_text(path, "new")

    if race is None:
        assert path.read_bytes() == b"new"
    elif race == "publish-error":
        assert raced is True
        assert path.read_bytes() == original_content
        assert not list(tmp_path.glob(f".{path.name}.*.rollback"))
    elif race == "same-inode-rewrite":
        assert raced is True
        assert path.read_bytes() == b"BBBB"
    else:
        assert raced is True
        assert path.read_bytes() == b"competitor"
        recoveries = list(tmp_path.glob(f".{path.name}.*.rollback"))
        assert len(recoveries) == 1
        assert recoveries[0].read_bytes() == original_content
    assert not list(tmp_path.glob(f".{path.name}.*.tmp"))


def test_atomic_write_text_fallback_stops_cleanup_after_parent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt" and sys.platform not in {"darwin", "linux"}:
        pytest.skip("safe exclusive rename is unavailable")
    monkeypatch.setattr(paths_mod, "_SUPPORTS_ANCHORED_DIRECTORY_OPERATIONS", False)
    parent = tmp_path / "output-parent"
    displaced = tmp_path / "displaced-parent"
    parent.mkdir()
    path = parent / "output.txt"
    path.write_bytes(b"original")
    original_rename = paths_mod.rename_noreplace
    replacement_temp_content = b"unrelated replacement temp"
    swapped = False

    def swap_parent_after_quarantine(source, destination, **kwargs) -> None:
        nonlocal swapped
        original_rename(source, destination, **kwargs)
        if Path(source) == path and Path(destination).name.endswith(".rollback"):
            staged = next(parent.glob(f".{path.name}.*.tmp"))
            parent.rename(displaced)
            parent.mkdir()
            (parent / staged.name).write_bytes(replacement_temp_content)
            (parent / "marker").write_bytes(b"replacement parent")
            swapped = True

    monkeypatch.setattr(
        paths_mod,
        "rename_noreplace",
        swap_parent_after_quarantine,
    )

    with pytest.raises(NotADirectoryError, match="Directory changed"):
        atomic_write_text(path, "new")

    assert swapped is True
    assert (parent / "marker").read_bytes() == b"replacement parent"
    replacement_temps = list(parent.glob(f".{path.name}.*.tmp"))
    assert len(replacement_temps) == 1
    assert replacement_temps[0].read_bytes() == replacement_temp_content
    assert len(list(displaced.glob(f".{path.name}.*.tmp"))) == 1
    recoveries = list(displaced.glob(f".{path.name}.*.rollback"))
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == b"original"


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


def test_atomic_write_captures_generation_before_directory_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    path = tmp_path / "output.txt"
    captured: list[paths_mod.FileGeneration] = []
    original_fsync = paths_mod.os.fsync

    def fail_after_capture(descriptor: int) -> None:
        if captured:
            raise OSError("directory sync failed")
        original_fsync(descriptor)

    monkeypatch.setattr(paths_mod.os, "fsync", fail_after_capture)

    with pytest.raises(OSError, match="directory sync failed"):
        atomic_write_text(
            path,
            "published",
            capture_generation=captured.append,
        )

    assert path.read_text(encoding="utf-8") == "published"
    assert captured == [regular_file_generation(path)]


@pytest.mark.parametrize(
    "content",
    [b"", b"owned generation\n"],
    ids=["empty", "nonempty"],
)
@pytest.mark.parametrize(
    "binding",
    ["content", "sha256"],
    ids=["exact-content", "sha256"],
)
def test_remove_file_generation_removes_matching_content(
    tmp_path: Path,
    content: bytes,
    binding: str,
) -> None:
    path = tmp_path / "output.txt"
    path.write_bytes(content)
    generation = regular_file_generation(path)
    assert generation is not None

    expected = (
        {"expected_content": content}
        if binding == "content"
        else {"expected_sha256": hashlib.sha256(content).hexdigest()}
    )

    assert paths_mod.remove_file_generation(path, generation, **expected)

    assert not path.exists()


@pytest.mark.parametrize(
    "mutation",
    ["replace", "same-size-rewrite"],
    ids=["replacement", "same-size-in-place-rewrite"],
)
def test_remove_file_generation_restores_mismatch(
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / "output.txt"
    original_content = b"original"
    concurrent_content = b"changed!"
    path.write_bytes(original_content)
    original_stat = path.stat()
    generation = regular_file_generation(path)
    assert generation is not None
    if mutation == "replace":
        replacement = tmp_path / "replacement.txt"
        replacement.write_bytes(concurrent_content)
        replacement.replace(path)
    else:
        path.write_bytes(concurrent_content)
        os.utime(
            path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )

    assert not paths_mod.remove_file_generation(
        path,
        generation,
        expected_content=original_content,
    )

    assert path.read_bytes() == concurrent_content


def test_remove_file_generation_preserves_concurrent_claimant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "output.txt"
    expected_content = b"expected"
    replaced_content = b"replaced"
    claimant_content = b"claimant"
    path.write_bytes(expected_content)
    generation = regular_file_generation(path)
    assert generation is not None
    replacement = tmp_path / "replacement.txt"
    replacement.write_bytes(replaced_content)
    replacement.replace(path)
    original_rename = paths_mod.rename_noreplace
    claimed = False

    def claim_before_restore(*args, **kwargs) -> None:
        nonlocal claimed
        source = Path(args[0])
        destination = Path(args[1])
        if (
            source.name.startswith(f".{path.name}.")
            and destination.name == path.name
            and not claimed
        ):
            path.write_bytes(claimant_content)
            claimed = True
        original_rename(*args, **kwargs)

    monkeypatch.setattr(paths_mod, "rename_noreplace", claim_before_restore)

    assert not paths_mod.remove_file_generation(
        path,
        generation,
        expected_content=expected_content,
    )

    assert claimed is True
    assert path.read_bytes() == claimant_content
    recoveries = list(tmp_path.glob(f".{path.name}.*.rollback"))
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == replaced_content


def test_remove_file_generation_rejects_parent_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    path = parent / "output.txt"
    content = b"expected"
    path.write_bytes(content)
    generation = regular_file_generation(path)
    assert generation is not None
    parent_identity = paths_mod.capture_directory_identity(parent)
    displaced = tmp_path / "displaced-parent"
    parent.rename(displaced)
    parent.mkdir()
    path.write_bytes(b"concurrent")

    with pytest.raises(NotADirectoryError, match="Directory changed"):
        paths_mod.remove_file_generation(
            path,
            generation,
            expected_content=content,
            expected_parent_identity=parent_identity,
        )

    assert path.read_bytes() == b"concurrent"
    assert (displaced / path.name).read_bytes() == content


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
