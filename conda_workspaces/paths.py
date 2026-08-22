"""Path validation helpers shared across workspace boundaries."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import sys
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, cast
from unicodedata import normalize

from .exceptions import FileRecoveryError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import BinaryIO


_SUPPORTS_ANCHORED_DIRECTORY_OPERATIONS = all(
    function in os.supports_dir_fd
    for function in (os.mkdir, os.open, os.rename, os.stat, os.unlink)
)

_WINDOWS_RESERVED_STEMS = {
    "AUX",
    "CON",
    "CONIN$",
    "CONOUT$",
    "NUL",
    "PRN",
    *(f"COM{digit}" for digit in "123456789¹²³"),
    *(f"LPT{digit}" for digit in "123456789¹²³"),
}

_LIBC = (
    ctypes.CDLL(None, use_errno=True) if sys.platform in {"darwin", "linux"} else None
)
_LINUX_RENAMEAT2_SYSCALLS = {
    "aarch64": 276,
    "armv6l": 382,
    "armv7l": 382,
    "i386": 353,
    "i486": 353,
    "i586": 353,
    "i686": 353,
    "loongarch64": 276,
    "ppc64": 357,
    "ppc64le": 357,
    "riscv64": 276,
    "s390x": 347,
    "x86_64": 316,
}
_ANY_FILE_IDENTITY = object()
_ANY_FILE_GENERATION = object()
_ANY_DIRECTORY_IDENTITY = object()

FileIdentity = tuple[int, int]
FileGeneration = tuple[int, int, int, int, int, int, int, int]
DirectoryIdentity = tuple[int, int, int, int, int]


def file_generation(value: os.stat_result) -> FileGeneration:
    """Return the canonical generation tuple for one file stat result."""
    ctime_ns = (
        getattr(value, "st_birthtime_ns", value.st_ctime_ns)
        if os.name == "nt"
        else value.st_ctime_ns
    )
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        ctime_ns,
    )


def directory_identity(value: os.stat_result) -> DirectoryIdentity:
    """Return stable ownership and inode identity for one directory."""
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
    )


def capture_directory_identity(
    path: Path,
    *,
    create: bool = False,
) -> DirectoryIdentity:
    """Open *path* without following links and return its live directory identity."""
    with anchored_directory(path, create=create) as directory_descriptor:
        opened = (
            os.fstat(directory_descriptor)
            if directory_descriptor is not None
            else path.lstat()
        )
        live = path.lstat()
        identity = directory_identity(opened)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(live.st_mode)
            or directory_identity(live) != identity
        ):
            raise NotADirectoryError(f"Path changed while it was opened: {path}")
        return identity


def require_directory_identity(
    path: Path,
    expected: DirectoryIdentity,
    *,
    directory_descriptor: int | None = None,
) -> None:
    """Require *path* and an optional open descriptor to name *expected*."""
    opened = (
        os.fstat(directory_descriptor)
        if directory_descriptor is not None
        else path.lstat()
    )
    live = path.lstat()
    if any(
        not stat.S_ISDIR(current.st_mode) or directory_identity(current) != expected
        for current in (opened, live)
    ):
        raise NotADirectoryError(f"Directory changed after it was captured: {path}")


def has_absolute_path_syntax(path: str) -> bool:
    """Return whether *path* is absolute using POSIX or Windows syntax."""
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


def parse_relative_posix_path(
    path: str,
    *,
    allow_parent: bool = False,
    require_canonical: bool = False,
) -> PurePosixPath:
    """Return *path* as a validated POSIX relative path.

    This lives at module level because archives, receipts, and imported
    manifests all need the same host-independent path syntax policy while
    raising domain-specific errors at their own call sites.
    """
    posix_path = PurePosixPath(path)
    windows_path = PureWindowsPath(path)
    windows_parts = tuple(windows_path.parts)
    posix_parts = tuple(posix_path.parts)
    if (
        not path
        or "\0" in path
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
        or not posix_parts
        or windows_parts != posix_parts
        or (require_canonical and posix_path.as_posix() != path)
        or (not allow_parent and any(part == ".." for part in posix_parts))
        or any(
            part != ".."
            and (
                part.endswith((" ", "."))
                or any(character in '<>:"|?*' for character in part)
                or any(
                    ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                    for character in part
                )
                or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS
                or len(part.encode("utf-8", errors="surrogatepass")) > 255
                or len(part.encode("utf-16-le", errors="surrogatepass")) // 2 > 255
            )
            for part in posix_parts
        )
    ):
        raise ValueError(f"Invalid relative path: {path!r}")
    return posix_path


def portable_path_key(path: PurePosixPath) -> tuple[str, ...]:
    """Return a host-independent key for archive topology comparisons."""
    return tuple(normalize("NFC", part).casefold() for part in path.parts)


def is_path_segment(value: str) -> bool:
    """Return whether *value* is one portable path segment.

    This is for manifest values that are used as filenames or directory
    names, not paths.  It checks POSIX and Windows parsing so a value that
    is harmless on the host OS but path-like elsewhere is still rejected.
    """
    try:
        return len(parse_relative_posix_path(value, require_canonical=True).parts) == 1
    except ValueError:
        return False


def resolve_relative_path(root: Path, path: PurePosixPath) -> Path:
    """Resolve *path* under *root*, rejecting symlink escapes."""
    root = root.resolve(strict=False)
    resolved = root.joinpath(*path.parts).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes root: {path!s}") from exc
    return resolved


def output_paths_collide(left: Path, right: Path) -> bool:
    """Return whether two output paths address the same file target."""
    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return left.samefile(right)
    except (FileNotFoundError, OSError):
        return False


def canonicalize_system_path_alias(path: Path) -> Path:
    """Resolve only a root-owned top-level directory alias.

    macOS exposes trusted system directories such as ``/tmp`` and ``/var`` as
    root-owned symbolic links. Workspace-controlled links below that boundary
    remain lexical so descriptor walking can reject them.
    """
    absolute = path.absolute()
    if os.name == "nt" or len(absolute.parts) < 2:
        return absolute
    first = Path(absolute.anchor, absolute.parts[1])
    try:
        first_stat = first.lstat()
    except OSError:
        return absolute
    if not stat.S_ISLNK(first_stat.st_mode) or first_stat.st_uid != 0:
        return absolute
    try:
        resolved = first.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError:
        return absolute
    if not stat.S_ISDIR(resolved_stat.st_mode) or resolved_stat.st_uid != 0:
        return absolute
    return resolved.joinpath(*absolute.parts[2:])


def validate_path_parent(path: Path) -> None:
    """Require the nearest existing parent of *path* to be a directory.

    Write previews use this read-only check before their mutation boundary so
    a file or broken symlink in the parent chain fails exactly as execution
    would.
    """
    parent = canonicalize_system_path_alias(path).parent
    while True:
        if parent.is_symlink():
            raise NotADirectoryError(f"Path parent is a symbolic link: {parent}")
        if parent.exists() and not parent.is_dir():
            raise NotADirectoryError(f"Path parent is not a directory: {parent}")
        if parent == parent.parent:
            return
        parent = parent.parent


def validate_file_output(path: Path) -> None:
    """Validate the existing shape of a prospective output file path."""
    validate_path_parent(path)
    if path.is_symlink():
        raise ValueError(f"Output path cannot be a symbolic link: {path}")
    if path.exists() and not path.is_file():
        raise IsADirectoryError(f"Output path is not a file: {path}")


def regular_file_generation(path: Path) -> FileGeneration | None:
    """Return the current regular output generation, or ``None`` if absent."""
    path = canonicalize_system_path_alias(path)
    validate_file_output(path)
    try:
        current = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(current.st_mode):
        raise ValueError(f"Output path is not a regular file: {path}")
    return file_generation(current)


def read_regular_file_bytes(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
    directory_descriptor: int | None = None,
) -> bytes:
    """Read one stable no-follow regular file under a byte limit."""
    return read_regular_file_bytes_with_generation(
        path,
        maximum_bytes=maximum_bytes,
        label=label,
        directory_descriptor=directory_descriptor,
    )[0]


def read_regular_file_bytes_with_generation(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
    directory_descriptor: int | None = None,
) -> tuple[bytes, FileGeneration]:
    """Read one stable no-follow regular file and return its generation."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    name = path.name if directory_descriptor is not None else path

    def current_stat() -> os.stat_result:
        if directory_descriptor is None:
            return path.lstat()
        return os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )

    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        current = current_stat()
        identity = opened.st_dev, opened.st_ino
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
        ):
            raise ValueError(f"Cannot read {label} safely: {path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read(maximum_bytes + 1)
            final = os.fstat(stream.fileno())
        current = current_stat()
        if (
            (final.st_dev, final.st_ino) != identity
            or final.st_size != opened.st_size
            or (
                final.st_mode,
                final.st_uid,
                final.st_gid,
                final.st_mtime_ns,
                final.st_ctime_ns,
            )
            != (
                opened.st_mode,
                opened.st_uid,
                opened.st_gid,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            or not stat.S_ISREG(current.st_mode)
            or file_generation(current) != file_generation(final)
            or (len(content) <= maximum_bytes and len(content) != opened.st_size)
        ):
            raise ValueError(f"The {label} changed while it was read: {path}")
        if len(content) > maximum_bytes:
            raise ValueError(
                f"{label} exceeds the maximum size of {maximum_bytes:,} bytes"
            )
        return content, file_generation(final)
    except OSError as exc:
        raise ValueError(f"Cannot read {label} safely: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def regular_file_sha256(
    path: Path,
    *,
    directory_descriptor: int | None = None,
    label: str = "file",
    maximum_bytes: int | None = None,
) -> tuple[str, FileGeneration]:
    """Hash one stable no-follow regular file and return its generation."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    name = path.name if directory_descriptor is not None else path

    def current_stat() -> os.stat_result:
        if directory_descriptor is None:
            return path.lstat()
        return os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )

    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        current = current_stat()
        identity = opened.st_dev, opened.st_ino
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
        ):
            raise ValueError(f"Cannot hash {label} safely: {path}")
        if maximum_bytes is not None and opened.st_size > maximum_bytes:
            raise ValueError(
                f"{label} exceeds the maximum size of {maximum_bytes:,} bytes"
            )
        digest = hashlib.sha256()
        hashed_bytes = 0
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            while chunk := stream.read(
                1024 * 1024
                if maximum_bytes is None
                else min(1024 * 1024, maximum_bytes + 1 - hashed_bytes)
            ):
                digest.update(chunk)
                hashed_bytes += len(chunk)
                if maximum_bytes is not None and hashed_bytes > maximum_bytes:
                    raise ValueError(
                        f"{label} exceeds the maximum size of {maximum_bytes:,} bytes"
                    )
            final = os.fstat(stream.fileno())
        current = current_stat()
        if (
            file_generation(final) != file_generation(opened)
            or not stat.S_ISREG(current.st_mode)
            or file_generation(current) != file_generation(final)
        ):
            raise ValueError(f"The {label} changed while it was hashed: {path}")
        return digest.hexdigest(), file_generation(final)
    except OSError as exc:
        raise ValueError(f"Cannot hash {label} safely: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def supports_anchored_directory_operations() -> bool:
    """Return whether public descriptor-relative directory APIs are available."""
    return _SUPPORTS_ANCHORED_DIRECTORY_OPERATIONS


def _rename_with_flags(
    source: str | Path,
    destination: str | Path,
    *,
    flag: int,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
) -> None:
    """Perform a flagged rename across two names with no owning path object."""
    operation = "atomic name exchange" if flag == 2 else "exclusive rename"
    if sys.platform not in {"darwin", "linux"}:
        raise NotImplementedError(f"{operation} is unavailable")
    assert _LIBC is not None
    if sys.platform == "linux":
        current_directory = -100
        function = getattr(_LIBC, "renameat2", None)
    elif sys.platform == "darwin":
        current_directory = -2
        function = _LIBC.renameatx_np
    source_descriptor = (
        source_dir_fd if source_dir_fd is not None else current_directory
    )
    destination_descriptor = (
        destination_dir_fd if destination_dir_fd is not None else current_directory
    )
    source_name = os.fsencode(source)
    destination_name = os.fsencode(destination)
    if function is None:
        syscall_number = _LINUX_RENAMEAT2_SYSCALLS.get(os.uname().machine)
        if syscall_number is None:
            raise NotImplementedError(
                f"{operation} is unavailable on this Linux architecture"
            ) from None
        result = _LIBC.syscall(
            ctypes.c_long(syscall_number),
            ctypes.c_int(source_descriptor),
            ctypes.c_char_p(source_name),
            ctypes.c_int(destination_descriptor),
            ctypes.c_char_p(destination_name),
            ctypes.c_uint(flag),
        )
    else:
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        function.restype = ctypes.c_int
        result = function(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
            flag,
        )
    if result != 0:
        error = ctypes.get_errno()
        if sys.platform == "linux" and error in {
            errno.EINVAL,
            errno.ENOSYS,
            errno.EOPNOTSUPP,
        }:
            raise NotImplementedError(
                f"{operation} is unavailable for these paths"
            ) from OSError(error, os.strerror(error), destination)
        raise OSError(error, os.strerror(error), destination)


def rename_noreplace(
    source: str | Path,
    destination: str | Path,
    *,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
) -> None:
    """Rename *source* only when *destination* does not exist."""
    if sys.platform not in {"darwin", "linux"}:
        if source_dir_fd is not None or destination_dir_fd is not None:
            raise NotImplementedError(
                "descriptor-relative exclusive rename unavailable"
            )
        os.rename(source, destination)
        return
    _rename_with_flags(
        source,
        destination,
        flag=1 if sys.platform == "linux" else 4,
        source_dir_fd=source_dir_fd,
        destination_dir_fd=destination_dir_fd,
    )


def remove_file_generation(
    path: Path,
    expected_generation: FileGeneration,
    *,
    expected_content: bytes | None = None,
    expected_sha256: str | None = None,
    directory_descriptor: int | None = None,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> bool:
    """Remove one file generation without unlinking a concurrent replacement.

    The live entry is first moved to an unpredictable quarantine name. A
    mismatched generation is restored with an exclusive rename, or retained at
    the recovery path when another entry has concurrently claimed *path*. If
    removing a matching generation fails, it remains at the reported recovery
    path rather than being reinstalled after validation.
    """
    if (expected_content is None) == (expected_sha256 is None):
        raise ValueError("Provide exactly one expected file content or SHA-256 digest")
    parent = path.parent
    recovery_name = f".{path.name}.{secrets.token_hex(16)}.rollback"
    recovery_path = parent / recovery_name
    with (
        nullcontext(directory_descriptor)
        if directory_descriptor is not None
        else anchored_directory(parent)
    ) as parent_descriptor:
        opened_parent = (
            parent.lstat() if parent_descriptor is None else os.fstat(parent_descriptor)
        )
        parent_identity = (
            directory_identity(opened_parent)
            if expected_parent_identity is None
            else expected_parent_identity
        )
        require_directory_identity(
            parent,
            parent_identity,
            directory_descriptor=parent_descriptor,
        )
        try:
            if parent_descriptor is None:
                rename_noreplace(path, recovery_path)
            else:
                rename_noreplace(
                    path.name,
                    recovery_name,
                    source_dir_fd=parent_descriptor,
                    destination_dir_fd=parent_descriptor,
                )
        except FileNotFoundError:
            return False

        if expected_content is not None:
            try:
                quarantined_content, quarantined_generation = (
                    read_regular_file_bytes_with_generation(
                        recovery_path,
                        maximum_bytes=max(1, len(expected_content)),
                        label="quarantined file",
                        directory_descriptor=parent_descriptor,
                    )
                )
            except ValueError:
                quarantined_content = None
                quarantined_generation = None
            content_matches = quarantined_content == expected_content
        else:
            try:
                quarantined_sha256, quarantined_generation = regular_file_sha256(
                    recovery_path,
                    label="quarantined file",
                    directory_descriptor=parent_descriptor,
                    maximum_bytes=expected_generation[5],
                )
            except ValueError:
                quarantined_sha256 = None
                quarantined_generation = None
            content_matches = quarantined_sha256 == expected_sha256
        if (
            content_matches
            and quarantined_generation is not None
            and quarantined_generation[:-1] == expected_generation[:-1]
        ):
            try:
                if parent_descriptor is None:
                    recovery_path.unlink()
                else:
                    os.unlink(recovery_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise FileRecoveryError(
                    path,
                    recovery_path,
                    "File generation could not be removed safely.",
                ) from exc
            if parent_descriptor is not None:
                os.fsync(parent_descriptor)
            require_directory_identity(
                parent,
                parent_identity,
                directory_descriptor=parent_descriptor,
            )
            return True

        try:
            if parent_descriptor is None:
                rename_noreplace(recovery_path, path)
            else:
                rename_noreplace(
                    recovery_name,
                    path.name,
                    source_dir_fd=parent_descriptor,
                    destination_dir_fd=parent_descriptor,
                )
        except FileNotFoundError:
            return False
        except (OSError, NotImplementedError) as exc:
            raise FileRecoveryError(
                path,
                recovery_path,
                "File generation could not be restored safely.",
            ) from exc
        if parent_descriptor is not None:
            os.fsync(parent_descriptor)
        require_directory_identity(
            parent,
            parent_identity,
            directory_descriptor=parent_descriptor,
        )
        return False


@contextmanager
def atomic_binary_writer_at(
    directory_descriptor: int,
    name: str,
    *,
    display_path: Path,
    expected_identity: FileIdentity | None | object = _ANY_FILE_IDENTITY,
    expected_generation: FileGeneration | None | object = _ANY_FILE_GENERATION,
    expected_sha256: str | None = None,
    capture_generation: Callable[[FileGeneration], None] | None = None,
) -> Iterator[BinaryIO]:
    """Atomically replace a file relative to an already anchored directory."""
    try:
        initial = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        initial_identity = None
        initial_generation = None
        mode = 0o644
    else:
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError(f"Output path is not a regular file: {display_path}")
        initial_identity = initial.st_dev, initial.st_ino
        initial_generation = file_generation(initial)
        mode = stat.S_IMODE(initial.st_mode) & 0o777
    if (
        expected_identity is not _ANY_FILE_IDENTITY
        and initial_identity != expected_identity
    ):
        raise ValueError(f"Output path changed before writing: {display_path}")
    if (
        expected_generation is not _ANY_FILE_GENERATION
        and initial_generation != expected_generation
    ):
        raise ValueError(f"Output path changed before writing: {display_path}")
    if initial_generation is None:
        initial_sha256 = None
    else:
        initial_sha256, hashed_generation = regular_file_sha256(
            display_path,
            directory_descriptor=directory_descriptor,
            label="existing output",
            maximum_bytes=initial.st_size,
        )
        if hashed_generation != initial_generation:
            raise ValueError(f"Output path changed before writing: {display_path}")
    if expected_sha256 is not None and initial_sha256 != expected_sha256:
        raise ValueError(f"Output path changed before writing: {display_path}")

    temporary_name = f".{name}.{secrets.token_hex(16)}.tmp"
    descriptor = -1
    published = False
    cleanup_temporary = True
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
            written = os.fstat(stream.fileno())
            temporary_identity = written.st_dev, written.st_ino
        temporary_sha256, temporary_generation = regular_file_sha256(
            display_path.parent / temporary_name,
            directory_descriptor=directory_descriptor,
            label="staged output",
            maximum_bytes=written.st_size,
        )
        if temporary_generation[:2] != temporary_identity:
            raise ValueError(f"Output changed while it was staged: {display_path}")

        try:
            current = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            current_identity = None
        else:
            if not stat.S_ISREG(current.st_mode):
                raise ValueError(f"Output path changed while writing: {display_path}")
            current_identity = current.st_dev, current.st_ino
        current_generation = (
            None if current_identity is None else file_generation(current)
        )
        if (
            current_identity != initial_identity
            or current_generation != initial_generation
        ):
            raise ValueError(f"Output path changed while writing: {display_path}")
        try:
            if initial_identity is None:
                rename_noreplace(
                    temporary_name,
                    name,
                    source_dir_fd=directory_descriptor,
                    destination_dir_fd=directory_descriptor,
                )
                cleanup_temporary = False
            else:
                if initial_generation is None:
                    raise RuntimeError("Existing output has no captured generation")
                _rename_with_flags(
                    temporary_name,
                    name,
                    flag=2,
                    source_dir_fd=directory_descriptor,
                    destination_dir_fd=directory_descriptor,
                )
        except NotImplementedError as exc:
            raise ValueError(
                f"Safe publication is unavailable for this output: {display_path}"
            ) from exc
        cleanup_temporary = False
        try:
            installed = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            installed = None
            installed_identity = None
            installed_sha256 = None
            installed_generation = None
        else:
            installed_identity = installed.st_dev, installed.st_ino
            try:
                installed_sha256, installed_generation = regular_file_sha256(
                    display_path,
                    directory_descriptor=directory_descriptor,
                    label="published output",
                    maximum_bytes=temporary_generation[5],
                )
            except ValueError:
                installed_sha256 = None
                installed_generation = None
        installed_matches = (
            installed_identity == temporary_identity
            and installed_generation is not None
            and installed_generation[:-1] == temporary_generation[:-1]
            and installed_sha256 == temporary_sha256
        )
        if initial_identity is not None:
            assert initial_generation is not None
            assert initial is not None
            try:
                displaced_sha256, displaced_generation = regular_file_sha256(
                    display_path.parent / temporary_name,
                    directory_descriptor=directory_descriptor,
                    label="displaced output",
                    maximum_bytes=initial.st_size,
                )
            except ValueError:
                displaced_sha256 = None
                displaced_generation = None
            displaced_matches = (
                displaced_generation is not None
                and displaced_generation[:-1] == initial_generation[:-1]
                and displaced_sha256 == initial_sha256
                and installed_matches
            )
            if not displaced_matches:
                recovery = display_path.parent / temporary_name
                try:
                    _rename_with_flags(
                        temporary_name,
                        name,
                        flag=2,
                        source_dir_fd=directory_descriptor,
                        destination_dir_fd=directory_descriptor,
                    )
                    restored = os.stat(
                        name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    recovery_stat = os.stat(
                        temporary_name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError as exc:
                    cleanup_temporary = True
                    raise ValueError(
                        f"Output path changed during publication: {display_path}"
                    ) from exc
                except (OSError, NotImplementedError) as exc:
                    raise FileRecoveryError(
                        display_path,
                        recovery,
                        "Output path changed during publication.",
                    ) from exc
                try:
                    recovery_sha256, recovery_generation = regular_file_sha256(
                        recovery,
                        directory_descriptor=directory_descriptor,
                        label="publication recovery output",
                        maximum_bytes=temporary_generation[5],
                    )
                except ValueError:
                    recovery_sha256 = None
                    recovery_generation = None
                recovery_matches = (
                    (recovery_stat.st_dev, recovery_stat.st_ino) == temporary_identity
                    and recovery_generation is not None
                    and recovery_generation[:-1] == temporary_generation[:-1]
                    and recovery_sha256 == temporary_sha256
                )
                if recovery_matches:
                    try:
                        os.unlink(temporary_name, dir_fd=directory_descriptor)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        raise FileRecoveryError(
                            display_path,
                            recovery,
                            "Publication recovery output could not be removed.",
                        ) from exc
                restored_message = (
                    ""
                    if (restored.st_dev, restored.st_ino) == initial_identity
                    else " Original output could not be restored."
                )
                if not recovery_matches:
                    raise FileRecoveryError(
                        display_path,
                        recovery,
                        f"Output path changed during publication.{restored_message}",
                    )
                raise ValueError(
                    f"Output path changed during publication.{restored_message}"
                )
        elif not installed_matches:
            if installed is None:
                raise ValueError(
                    f"Output path changed during publication: {display_path}"
                )
            if installed_identity != temporary_identity:
                raise ValueError(
                    f"Output path changed during publication: {display_path}"
                )
            removed = remove_file_generation(
                display_path,
                temporary_generation,
                expected_sha256=temporary_sha256,
                directory_descriptor=directory_descriptor,
            )
            if removed:
                raise ValueError(
                    f"Output path changed during publication: {display_path}"
                )
            raise FileRecoveryError(
                display_path,
                display_path,
                "Output path changed during publication.",
            )
        assert installed_generation is not None
        try:
            final = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            if initial_identity is not None:
                raise FileRecoveryError(
                    display_path,
                    display_path.parent / temporary_name,
                    "Output path changed during publication.",
                ) from exc
            raise
        if (final.st_dev, final.st_ino) != temporary_identity or file_generation(
            final
        ) != installed_generation:
            if initial_identity is not None:
                raise FileRecoveryError(
                    display_path,
                    display_path.parent / temporary_name,
                    "Output path changed during publication.",
                )
            raise ValueError(f"Output path changed during publication: {display_path}")
        if capture_generation is not None:
            try:
                capture_generation(installed_generation)
            except BaseException as exc:
                if initial_identity is not None:
                    raise FileRecoveryError(
                        display_path,
                        display_path.parent / temporary_name,
                        "Published output generation could not be recorded.",
                    ) from exc
                try:
                    remove_file_generation(
                        display_path,
                        installed_generation,
                        expected_sha256=temporary_sha256,
                        directory_descriptor=directory_descriptor,
                    )
                except FileRecoveryError:
                    raise
                except (OSError, ValueError) as rollback_error:
                    raise FileRecoveryError(
                        display_path,
                        display_path,
                        "Published output could not be rolled back after its"
                        " generation capture failed.",
                    ) from rollback_error
                raise
        if initial_identity is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise FileRecoveryError(
                    display_path,
                    display_path.parent / temporary_name,
                    "Published output recovery could not be removed.",
                ) from exc
        published = True
        os.fsync(directory_descriptor)
    finally:
        active_error = sys.exc_info()[1]
        if descriptor >= 0:
            os.close(descriptor)
        if not published and cleanup_temporary:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_error = FileRecoveryError(
                    display_path,
                    display_path.parent / temporary_name,
                    "Staged output could not be removed.",
                )
                if isinstance(active_error, FileRecoveryError):
                    raise active_error.combine(
                        cleanup_error,
                        reason="Multiple publication recovery entries were retained.",
                    ) from exc
                raise cleanup_error from exc
        elif not published and not cleanup_temporary and initial_identity is not None:
            try:
                retained = os.stat(
                    temporary_name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            except OSError as inspection_error:
                retained_error = FileRecoveryError(
                    display_path,
                    display_path.parent / temporary_name,
                    "Publication recovery entry could not be inspected.",
                )
                if isinstance(active_error, FileRecoveryError):
                    raise active_error.combine(
                        retained_error,
                        reason="Publication recovery could not be inspected.",
                    ) from inspection_error
                raise retained_error from inspection_error
            else:
                if stat.S_ISREG(retained.st_mode):
                    retained_error = FileRecoveryError(
                        display_path,
                        display_path.parent / temporary_name,
                        "Publication failed with a retained recovery entry.",
                    )
                    if isinstance(active_error, FileRecoveryError):
                        if str(retained_error.recovery_path) not in {
                            str(path) for path in active_error.recovery_paths
                        }:
                            raise active_error.combine(
                                retained_error,
                                reason=(
                                    "Multiple publication recovery entries were"
                                    " retained."
                                ),
                            ) from active_error
                    elif active_error is not None:
                        raise retained_error from active_error


def atomic_write_text_at(
    directory_descriptor: int,
    name: str,
    content: str,
    *,
    display_path: Path,
    encoding: str = "utf-8",
    expected_identity: FileIdentity | None | object = _ANY_FILE_IDENTITY,
    expected_generation: FileGeneration | None | object = _ANY_FILE_GENERATION,
    expected_sha256: str | None = None,
    capture_generation: Callable[[FileGeneration], None] | None = None,
) -> None:
    """Write text relative to an already anchored directory descriptor."""
    with atomic_binary_writer_at(
        directory_descriptor,
        name,
        display_path=display_path,
        expected_identity=expected_identity,
        expected_generation=expected_generation,
        expected_sha256=expected_sha256,
        capture_generation=capture_generation,
    ) as stream:
        stream.write(content.encode(encoding))


@contextmanager
def anchored_directory(path: Path, *, create: bool = False) -> Iterator[int | None]:
    """Yield a verified directory descriptor when the platform supports one.

    A ``None`` descriptor lets callers retain their platform-specific fallback
    without pretending that path-only operations have descriptor semantics.
    Existing path components are opened one at a time without following links.
    """
    if not supports_anchored_directory_operations():
        if create:
            validate_path_parent(path)
            path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise NotADirectoryError(f"Path is not a regular directory: {path}")
        yield None
        return

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    absolute = canonicalize_system_path_alias(path)
    descriptor = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        opened = os.fstat(descriptor)
        try:
            current = absolute.lstat()
        except FileNotFoundError as exc:
            raise NotADirectoryError(
                f"Path changed while it was opened: {absolute}"
            ) from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise NotADirectoryError(f"Path changed while it was opened: {path}")
        yield descriptor
    finally:
        os.close(descriptor)


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    expected_identity: FileIdentity | None | object = _ANY_FILE_IDENTITY,
    expected_generation: FileGeneration | None | object = _ANY_FILE_GENERATION,
    expected_sha256: str | None = None,
    expected_parent_identity: DirectoryIdentity | object = _ANY_DIRECTORY_IDENTITY,
    capture_generation: Callable[[FileGeneration], None] | None = None,
) -> None:
    """Replace a regular text file without following links or other claimants."""
    with atomic_binary_writer(
        path,
        expected_identity=expected_identity,
        expected_generation=expected_generation,
        expected_sha256=expected_sha256,
        expected_parent_identity=expected_parent_identity,
        capture_generation=capture_generation,
    ) as stream:
        stream.write(content.encode(encoding))


@contextmanager
def atomic_binary_writer(
    path: Path,
    *,
    expected_identity: FileIdentity | None | object = _ANY_FILE_IDENTITY,
    expected_generation: FileGeneration | None | object = _ANY_FILE_GENERATION,
    expected_sha256: str | None = None,
    expected_parent_identity: DirectoryIdentity | object = _ANY_DIRECTORY_IDENTITY,
    capture_generation: Callable[[FileGeneration], None] | None = None,
) -> Iterator[BinaryIO]:
    """Yield a binary stream and publish it as one guarded file generation."""
    path = canonicalize_system_path_alias(path)
    if path.is_symlink():
        raise ValueError(f"Output path cannot be a symbolic link: {path}")
    validate_file_output(path)
    with anchored_directory(path.parent, create=True) as parent_descriptor:
        if parent_descriptor is not None:
            opened_parent = os.fstat(parent_descriptor)
            if (
                expected_parent_identity is not _ANY_DIRECTORY_IDENTITY
                and directory_identity(opened_parent) != expected_parent_identity
            ):
                raise ValueError(f"Output parent changed before writing: {path.parent}")
            with atomic_binary_writer_at(
                parent_descriptor,
                path.name,
                display_path=path,
                expected_identity=expected_identity,
                expected_generation=expected_generation,
                expected_sha256=expected_sha256,
                capture_generation=capture_generation,
            ) as stream:
                yield stream
            final = os.stat(
                path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            live_parent = path.parent.lstat()
            live_final = path.lstat()
            if (
                not stat.S_ISDIR(live_parent.st_mode)
                or (live_parent.st_dev, live_parent.st_ino)
                != (opened_parent.st_dev, opened_parent.st_ino)
                or (
                    expected_parent_identity is not _ANY_DIRECTORY_IDENTITY
                    and directory_identity(live_parent) != expected_parent_identity
                )
                or not stat.S_ISREG(live_final.st_mode)
                or (live_final.st_dev, live_final.st_ino)
                != (final.st_dev, final.st_ino)
                or file_generation(live_final) != file_generation(final)
            ):
                raise ValueError(f"Output path changed during publication: {path}")
            return

    parent = path.parent
    parent_stat = parent.lstat()
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise NotADirectoryError(f"Output parent is not a directory: {parent}")
    fallback_parent_identity = directory_identity(parent_stat)
    if (
        expected_parent_identity is not _ANY_DIRECTORY_IDENTITY
        and fallback_parent_identity != expected_parent_identity
    ):
        raise ValueError(f"Output parent changed before writing: {parent}")
    require_directory_identity(parent, fallback_parent_identity)
    initial = path.lstat() if path.exists() else None
    require_directory_identity(parent, fallback_parent_identity)
    if initial is not None and not stat.S_ISREG(initial.st_mode):
        raise ValueError(f"Output path is not a regular file: {path}")
    initial_identity = (initial.st_dev, initial.st_ino) if initial is not None else None
    initial_generation = file_generation(initial) if initial is not None else None
    if (
        expected_identity is not _ANY_FILE_IDENTITY
        and initial_identity != expected_identity
    ):
        raise ValueError(f"Output path changed before writing: {path}")
    if (
        expected_generation is not _ANY_FILE_GENERATION
        and initial_generation != expected_generation
    ):
        raise ValueError(f"Output path changed before writing: {path}")
    if initial_generation is None:
        initial_sha256 = None
    else:
        assert initial is not None
        if os.name != "nt" and sys.platform not in {"darwin", "linux"}:
            raise NotImplementedError(
                "Safe replacement is unavailable on this platform"
            )
        require_directory_identity(parent, fallback_parent_identity)
        initial_sha256, hashed_generation = regular_file_sha256(
            path,
            label="existing output",
            maximum_bytes=initial.st_size,
        )
        require_directory_identity(parent, fallback_parent_identity)
        if hashed_generation != initial_generation:
            raise ValueError(f"Output path changed before writing: {path}")
    if expected_sha256 is not None and initial_sha256 != expected_sha256:
        raise ValueError(f"Output path changed before writing: {path}")
    temp_name = None
    recovery_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_name = stream.name
            require_directory_identity(parent, fallback_parent_identity)
            yield cast("BinaryIO", stream)
            stream.flush()
            os.fsync(stream.fileno())
            mode = (
                stat.S_IMODE(initial.st_mode) & 0o777 if initial is not None else 0o644
            )
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), mode)
            else:
                require_directory_identity(parent, fallback_parent_identity)
                os.chmod(temp_name, mode)
                require_directory_identity(parent, fallback_parent_identity)
            written = os.fstat(stream.fileno())
            temporary_identity = written.st_dev, written.st_ino
        require_directory_identity(parent, fallback_parent_identity)
        temporary_sha256, temporary_generation = regular_file_sha256(
            Path(temp_name),
            label="staged output",
            maximum_bytes=written.st_size,
        )
        require_directory_identity(parent, fallback_parent_identity)
        if temporary_generation[:2] != temporary_identity:
            raise ValueError(f"Output changed while it was staged: {path}")
        current = path.lstat() if path.exists() else None
        require_directory_identity(parent, fallback_parent_identity)
        if (
            path.is_symlink()
            or (current is None) != (initial is None)
            or (
                current is not None
                and initial is not None
                and file_generation(current) != initial_generation
            )
        ):
            raise ValueError(f"Output path changed while writing: {path}")
        if initial is None:
            require_directory_identity(parent, fallback_parent_identity)
            rename_noreplace(temp_name, path)
            require_directory_identity(parent, fallback_parent_identity)
            temp_name = None
            try:
                final_sha256, final_generation = regular_file_sha256(
                    path,
                    label="published output",
                    maximum_bytes=temporary_generation[5],
                )
            except ValueError:
                current_output = path.lstat() if path.exists() else None
                final_sha256 = None
                final_generation = (
                    file_generation(current_output)
                    if current_output is not None
                    and stat.S_ISREG(current_output.st_mode)
                    else None
                )
            require_directory_identity(parent, fallback_parent_identity)
            output_matches = (
                final_generation is not None
                and final_generation[:2] == temporary_identity
                and final_generation[:-1] == temporary_generation[:-1]
                and final_sha256 == temporary_sha256
            )
            if not output_matches:
                if final_generation is None:
                    raise ValueError(f"Output path changed during publication: {path}")
                if final_generation[:2] != temporary_identity:
                    raise ValueError(f"Output path changed during publication: {path}")
                removed = remove_file_generation(
                    path,
                    temporary_generation,
                    expected_sha256=temporary_sha256,
                    expected_parent_identity=fallback_parent_identity,
                )
                if removed:
                    raise ValueError(f"Output path changed during publication: {path}")
                raise FileRecoveryError(
                    path,
                    path,
                    "Output path changed during publication.",
                )
        else:
            assert initial_generation is not None
            recovery_path = path.with_name(
                f".{path.name}.{secrets.token_hex(16)}.rollback"
            )
            require_directory_identity(parent, fallback_parent_identity)
            rename_noreplace(path, recovery_path)
            require_directory_identity(parent, fallback_parent_identity)
            try:
                require_directory_identity(parent, fallback_parent_identity)
                recovery_sha256, recovery_generation = regular_file_sha256(
                    recovery_path,
                    label="displaced output",
                    maximum_bytes=initial.st_size,
                )
                require_directory_identity(parent, fallback_parent_identity)
                if (
                    recovery_generation[:-1] != initial_generation[:-1]
                    or recovery_sha256 != initial_sha256
                ):
                    raise ValueError(f"Output path changed during publication: {path}")
                require_directory_identity(parent, fallback_parent_identity)
                rename_noreplace(temp_name, path)
                require_directory_identity(parent, fallback_parent_identity)
                temp_name = None
                require_directory_identity(parent, fallback_parent_identity)
                final_sha256, final_generation = regular_file_sha256(
                    path,
                    label="published output",
                    maximum_bytes=temporary_generation[5],
                )
                require_directory_identity(parent, fallback_parent_identity)
                if (
                    final_generation[:2] != temporary_identity
                    or final_generation[:-1] != temporary_generation[:-1]
                    or final_sha256 != temporary_sha256
                ):
                    raise ValueError(f"Output path changed during publication: {path}")
                if capture_generation is not None:
                    try:
                        capture_generation(final_generation)
                    except BaseException as capture_error:
                        raise FileRecoveryError(
                            path,
                            recovery_path,
                            "Published output generation could not be recorded.",
                        ) from capture_error
            except BaseException as exc:
                if isinstance(exc, FileRecoveryError):
                    raise
                try:
                    require_directory_identity(parent, fallback_parent_identity)
                    rename_noreplace(recovery_path, path)
                    require_directory_identity(parent, fallback_parent_identity)
                except FileNotFoundError as restore_exc:
                    recovery_path = None
                    if isinstance(exc, (OSError, ValueError)):
                        raise ValueError(
                            f"Output path changed during publication: {path}"
                        ) from restore_exc
                    raise
                except (OSError, ValueError) as restore_exc:
                    raise FileRecoveryError(
                        path,
                        recovery_path,
                        "Output path changed during publication.",
                    ) from restore_exc
                recovery_path = None
                if isinstance(exc, (OSError, ValueError)):
                    raise ValueError(
                        f"Output path changed during publication: {path}"
                    ) from exc
                raise
            try:
                require_directory_identity(parent, fallback_parent_identity)
                recovery_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise FileRecoveryError(
                    path,
                    recovery_path,
                    "Published output recovery could not be removed.",
                ) from exc
            require_directory_identity(parent, fallback_parent_identity)
            recovery_path = None
        final = path.lstat()
        require_directory_identity(parent, fallback_parent_identity)
        if (
            not stat.S_ISREG(final.st_mode)
            or (
                final.st_dev,
                final.st_ino,
            )
            != temporary_identity
            or file_generation(final) != final_generation
        ):
            raise ValueError(f"Output path changed during publication: {path}")
        if initial is None and capture_generation is not None:
            try:
                capture_generation(final_generation)
            except BaseException:
                try:
                    remove_file_generation(
                        path,
                        final_generation,
                        expected_sha256=temporary_sha256,
                        expected_parent_identity=fallback_parent_identity,
                    )
                except FileRecoveryError:
                    raise
                except (OSError, ValueError) as rollback_error:
                    raise FileRecoveryError(
                        path,
                        path,
                        "Published output could not be rolled back after its"
                        " generation capture failed.",
                    ) from rollback_error
                raise
        require_directory_identity(parent, fallback_parent_identity)
    finally:
        active_error = sys.exc_info()[1]
        if temp_name is not None:
            try:
                require_directory_identity(parent, fallback_parent_identity)
                os.unlink(temp_name)
                require_directory_identity(parent, fallback_parent_identity)
            except FileNotFoundError:
                pass
            except OSError as exc:
                try:
                    require_directory_identity(parent, fallback_parent_identity)
                except (OSError, ValueError):
                    pass
                else:
                    cleanup_error = FileRecoveryError(
                        path,
                        Path(temp_name),
                        "Staged output could not be removed.",
                    )
                    if isinstance(active_error, FileRecoveryError):
                        raise active_error.combine(
                            cleanup_error,
                            reason=(
                                "Multiple publication recovery entries were retained."
                            ),
                        ) from exc
                    raise cleanup_error from exc


def validate_directory_output(path: Path) -> None:
    """Validate the existing shape of a prospective output directory path."""
    validate_path_parent(path)
    if path.exists() and not path.is_dir():
        raise FileExistsError(f"Output path is not a directory: {path}")
    if path.is_symlink() and not path.exists():
        raise FileExistsError(f"Output path is a broken symlink: {path}")
