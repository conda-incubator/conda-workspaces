"""Serialized publication of workspace manifests and lockfiles."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.gateways.disk.lock import LOCK_BYTE, lock

from .exceptions import CondaWorkspacesError
from .lockfile import MAX_LOCKFILE_BYTES, lockfile_path, validate_lockfile_output
from .manifests.base import MAX_MANIFEST_BYTES, ManifestParser
from .paths import (
    anchored_directory,
    atomic_write_text,
    atomic_write_text_at,
    read_regular_file_bytes_with_generation,
    regular_file_generation,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import BinaryIO

    from .context import WorkspaceContext
    from .paths import FileGeneration


@dataclass
class WorkspacePublication:
    """Publish one prospective workspace state under a workspace-wide lock."""

    ctx: WorkspaceContext
    manifest_path: Path
    original_text: str
    updated_text: str
    operation: str
    started: bool = field(default=False, init=False)
    _guarded: bool = field(default=False, init=False, repr=False)
    _guard_identity_validator: Callable[[], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _root_descriptor: int | None = field(default=None, init=False, repr=False)
    _manifest_generation: FileGeneration | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _lockfile_content: bytes | None = field(default=None, init=False, repr=False)
    _lockfile_generation: FileGeneration | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _lockfile_generation_captured: bool = field(default=False, init=False, repr=False)

    @property
    def lock_path(self) -> Path:
        """Return the workspace-wide publication lock path."""
        return self.ctx.root / ".conda" / "workspace.lock"

    @property
    def guarded_root_descriptor(self) -> int | None:
        """Return the anchored workspace root while :meth:`guard` is held."""
        if not self._guarded:
            raise RuntimeError("Workspace publication guard is not held")
        return self._root_descriptor

    @classmethod
    def from_current_manifest(
        cls,
        ctx: WorkspaceContext,
        operation: str,
    ) -> WorkspacePublication:
        """Capture the current manifest generation for a lock-only publisher."""
        manifest_path = Path(ctx.config.manifest_path)
        cls.validate_manifest_path(manifest_path)
        manifest_text = ctx.config._manifest_text
        if manifest_text is None:
            manifest_text = ManifestParser.read_manifest_text(manifest_path)
        return cls(
            ctx,
            manifest_path,
            manifest_text,
            manifest_text,
            operation,
        )

    @staticmethod
    def validate_manifest_path(path: Path) -> None:
        """Reject a manifest path that would publish through a symlink."""
        if path.is_symlink():
            raise CondaWorkspacesError(
                f"Workspace manifest cannot be a symlink: {path}"
            )

    def validate_manifest_generation(self) -> None:
        """Reject a manifest that changed after this publication was prepared."""
        if self._guard_identity_validator is not None:
            self._guard_identity_validator()
        try:
            current_content, generation = read_regular_file_bytes_with_generation(
                self.manifest_path,
                maximum_bytes=MAX_MANIFEST_BYTES,
                label="workspace manifest",
                directory_descriptor=self._root_descriptor,
            )
            current_text = current_content.decode("utf-8")
        except ValueError as exc:
            raise CondaWorkspacesError(
                f"Workspace manifest cannot be read safely: {self.manifest_path}"
            ) from exc
        expected_text = self.updated_text if self.started else self.original_text
        if current_text != expected_text:
            raise CondaWorkspacesError(
                "Workspace manifest changed while the "
                f"{self.operation} was being prepared. Retry the {self.operation}."
            )
        self._manifest_generation = generation

    def _validate_lock_identity(
        self,
        opened_stat: os.stat_result,
        state_stat: os.stat_result,
    ) -> None:
        """Require the lock pathname to still identify the opened file."""
        try:
            current_state_stat = self.lock_path.parent.lstat()
        except FileNotFoundError as exc:
            raise CondaWorkspacesError(
                "Workspace state directory changed while opening: "
                f"{self.lock_path.parent}"
            ) from exc
        if stat.S_ISLNK(current_state_stat.st_mode):
            raise CondaWorkspacesError(
                f"Workspace state directory is a symlink: {self.lock_path.parent}"
            )
        if not stat.S_ISDIR(current_state_stat.st_mode) or (
            current_state_stat.st_dev,
            current_state_stat.st_ino,
        ) != (state_stat.st_dev, state_stat.st_ino):
            raise CondaWorkspacesError(
                "Workspace state directory changed while opening: "
                f"{self.lock_path.parent}"
            )
        try:
            path_stat = self.lock_path.lstat()
        except FileNotFoundError as exc:
            raise CondaWorkspacesError(
                f"Workspace publication lock changed while opening: {self.lock_path}"
            ) from exc
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or opened_stat.st_nlink != 1
            or path_stat.st_nlink != 1
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise CondaWorkspacesError(
                f"Workspace publication lock changed while opening: {self.lock_path}"
            )

    def _capture_lockfile_generation(self) -> None:
        """Bind publication to the current canonical lockfile generation."""
        path = lockfile_path(self.ctx)
        try:
            if self._root_descriptor is None:
                generation = regular_file_generation(path)
            else:
                try:
                    current = os.stat(
                        path.name,
                        dir_fd=self._root_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    generation = None
                else:
                    if not stat.S_ISREG(current.st_mode):
                        raise ValueError(
                            f"Workspace lockfile is not a regular file: {path}"
                        )
                    generation = (
                        current.st_dev,
                        current.st_ino,
                        current.st_mode,
                        current.st_uid,
                        current.st_gid,
                        current.st_size,
                        current.st_mtime_ns,
                        current.st_ctime_ns,
                    )
        except (OSError, ValueError) as exc:
            raise CondaWorkspacesError(
                f"Workspace lockfile cannot be inspected safely: {path}"
            ) from exc
        self._lockfile_content = None
        self._lockfile_generation = generation
        self._lockfile_generation_captured = True

    def _validate_anchored_lock_identity(
        self,
        root_descriptor: int,
        state_descriptor: int,
        lock_stat: os.stat_result,
    ) -> None:
        """Require the live workspace path to identify the anchored lock."""
        root_stat = os.fstat(root_descriptor)
        state_stat = os.fstat(state_descriptor)
        try:
            live_root = self.ctx.root.lstat()
            live_state = os.stat(
                ".conda",
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            live_lock = os.stat(
                self.lock_path.name,
                dir_fd=state_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            raise CondaWorkspacesError(
                f"Workspace publication lock changed while opening: {self.lock_path}"
            ) from exc
        if (
            not stat.S_ISDIR(live_root.st_mode)
            or (live_root.st_dev, live_root.st_ino)
            != (root_stat.st_dev, root_stat.st_ino)
            or not stat.S_ISDIR(live_state.st_mode)
            or (live_state.st_dev, live_state.st_ino)
            != (state_stat.st_dev, state_stat.st_ino)
            or not stat.S_ISREG(live_lock.st_mode)
            or lock_stat.st_nlink != 1
            or live_lock.st_nlink != 1
            or (live_lock.st_dev, live_lock.st_ino)
            != (lock_stat.st_dev, lock_stat.st_ino)
        ):
            raise CondaWorkspacesError(
                f"Workspace publication lock changed while opening: {self.lock_path}"
            )

    @contextmanager
    def _hold_guard(
        self,
        stream: BinaryIO,
        validate_identity: Callable[[], None],
        *,
        root_descriptor: int | None = None,
    ) -> Iterator[None]:
        """Hold conda's byte lock after validating its anchored identity."""
        validate_identity()
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise CondaWorkspacesError(
                f"Workspace publication lock is hardlinked: {self.lock_path}"
            )
        stream.seek(0, 2)
        if stream.tell() <= LOCK_BYTE:
            stream.write(b"\0" * (LOCK_BYTE + 1 - stream.tell()))
            stream.flush()
        with conda_context._override("no_lock", False), lock(stream):
            self._guard_identity_validator = validate_identity
            self._root_descriptor = root_descriptor
            validate_identity()
            self.validate_manifest_generation()
            self._capture_lockfile_generation()
            self._guarded = True
            try:
                yield
            finally:
                try:
                    validate_identity()
                finally:
                    self._guarded = False
                    self._root_descriptor = None
                    self._guard_identity_validator = None

    @contextmanager
    def guard(
        self,
        *,
        expected_root_generation: tuple[int, int, int, int, int, int] | None = None,
    ) -> Iterator[None]:
        """Lock publication and reject a concurrently changed manifest."""
        if self._guarded:
            raise RuntimeError("Workspace publication guard is already held")
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            with anchored_directory(self.ctx.root) as root_descriptor:
                root_stat = (
                    self.ctx.root.lstat()
                    if root_descriptor is None
                    else os.fstat(root_descriptor)
                )
                root_generation = (
                    root_stat.st_dev,
                    root_stat.st_ino,
                    root_stat.st_size,
                    root_stat.st_mode,
                    root_stat.st_mtime_ns,
                    root_stat.st_ctime_ns,
                )
                if (
                    expected_root_generation is not None
                    and root_generation != expected_root_generation
                ):
                    raise CondaWorkspacesError(
                        "Workspace root changed while the "
                        f"{self.operation} inputs were collected. Retry the "
                        f"{self.operation}."
                    )
                if root_descriptor is None:
                    if self.lock_path.is_symlink():
                        raise CondaWorkspacesError(
                            f"Workspace publication lock is a symlink: {self.lock_path}"
                        )
                    self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                    if self.lock_path.parent.is_symlink():
                        raise CondaWorkspacesError(
                            "Workspace state directory is a symlink: "
                            f"{self.lock_path.parent}"
                        )
                    state_stat = self.lock_path.parent.lstat()
                    if not stat.S_ISDIR(state_stat.st_mode):
                        raise CondaWorkspacesError(
                            "Workspace state path is not a directory: "
                            f"{self.lock_path.parent}"
                        )
                    descriptor = -1
                    try:
                        descriptor = os.open(self.lock_path, flags, 0o600)
                    except OSError as exc:
                        if self.lock_path.is_symlink():
                            raise CondaWorkspacesError(
                                "Workspace publication lock is a symlink: "
                                f"{self.lock_path}"
                            ) from exc
                        raise
                    try:
                        opened_stat = os.fstat(descriptor)
                        self._validate_lock_identity(opened_stat, state_stat)
                        stream = os.fdopen(descriptor, "r+b")
                        descriptor = -1
                        with stream:
                            with self._hold_guard(
                                stream,
                                lambda: self._validate_lock_identity(
                                    opened_stat,
                                    state_stat,
                                ),
                            ):
                                yield
                    finally:
                        if descriptor >= 0:
                            os.close(descriptor)
                    return

                try:
                    os.mkdir(".conda", mode=0o700, dir_fd=root_descriptor)
                except FileExistsError:
                    pass
                state_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    state_descriptor = os.open(
                        ".conda",
                        state_flags,
                        dir_fd=root_descriptor,
                    )
                except OSError as exc:
                    raise CondaWorkspacesError(
                        f"Workspace state directory is unsafe: {self.lock_path.parent}"
                    ) from exc
                try:
                    descriptor = -1
                    try:
                        descriptor = os.open(
                            self.lock_path.name,
                            flags,
                            0o600,
                            dir_fd=state_descriptor,
                        )
                    except OSError as exc:
                        try:
                            lock_entry = os.stat(
                                self.lock_path.name,
                                dir_fd=state_descriptor,
                                follow_symlinks=False,
                            )
                        except OSError:
                            lock_entry = None
                        if lock_entry is not None and stat.S_ISLNK(lock_entry.st_mode):
                            raise CondaWorkspacesError(
                                "Workspace publication lock is a symlink: "
                                f"{self.lock_path}"
                            ) from exc
                        raise CondaWorkspacesError(
                            f"Workspace publication lock is unsafe: {self.lock_path}"
                        ) from exc
                    try:
                        opened_stat = os.fstat(descriptor)
                        self._validate_anchored_lock_identity(
                            root_descriptor,
                            state_descriptor,
                            opened_stat,
                        )
                        stream = os.fdopen(descriptor, "r+b")
                        descriptor = -1
                        with stream:
                            with self._hold_guard(
                                stream,
                                lambda: self._validate_anchored_lock_identity(
                                    root_descriptor,
                                    state_descriptor,
                                    opened_stat,
                                ),
                                root_descriptor=root_descriptor,
                            ):
                                yield
                    finally:
                        if descriptor >= 0:
                            os.close(descriptor)
                finally:
                    os.close(state_descriptor)
        except NotADirectoryError as exc:
            raise CondaWorkspacesError(
                f"Workspace root changed while opening: {self.ctx.root}"
            ) from exc
        except OSError as exc:
            if self.lock_path.is_symlink():
                raise CondaWorkspacesError(
                    f"Workspace publication lock is a symlink: {self.lock_path}"
                ) from exc
            raise

    def publish_manifest(self) -> None:
        """Publish the staged manifest while :meth:`guard` is held."""
        if not self._guarded:
            raise RuntimeError("Workspace publication guard is not held")
        self.validate_manifest_generation()
        expected_generation = self._manifest_generation
        if expected_generation is None:
            raise RuntimeError("Workspace manifest generation was not captured")
        if self.updated_text != self.original_text:
            try:
                if self._root_descriptor is None:
                    atomic_write_text(
                        self.manifest_path,
                        self.updated_text,
                        expected_generation=expected_generation,
                    )
                else:
                    atomic_write_text_at(
                        self._root_descriptor,
                        self.manifest_path.name,
                        self.updated_text,
                        display_path=self.manifest_path,
                        expected_generation=expected_generation,
                    )
            except ValueError as exc:
                raise CondaWorkspacesError(
                    "Workspace manifest changed before publication."
                ) from exc
            self.started = True
            self.validate_manifest_generation()
        else:
            self.started = True

    def read_lockfile_bytes(self) -> bytes:
        """Read one stable lockfile snapshot from the guarded workspace root."""
        if not self._guarded:
            raise RuntimeError("Workspace publication guard is not held")
        self.validate_manifest_generation()
        path = lockfile_path(self.ctx)
        content, generation = read_regular_file_bytes_with_generation(
            path,
            maximum_bytes=MAX_LOCKFILE_BYTES,
            label="workspace lockfile",
            directory_descriptor=self._root_descriptor,
        )
        if self._lockfile_generation_captured and (
            generation != self._lockfile_generation
        ):
            raise CondaWorkspacesError(
                "Workspace lockfile changed while it was being read."
            )
        self._lockfile_content = content
        self._lockfile_generation = generation
        self.validate_manifest_generation()
        return content

    def publish_lockfile(self, content: str) -> None:
        """Publish the staged manifest and rendered lockfile together."""
        publication_guard = nullcontext() if self._guarded else self.guard()
        with publication_guard:
            self.validate_manifest_generation()
            path = lockfile_path(self.ctx)
            expected_lock_generation = self._lockfile_generation
            if self._lockfile_content is not None:
                try:
                    current_content, current_generation = (
                        read_regular_file_bytes_with_generation(
                            path,
                            maximum_bytes=MAX_LOCKFILE_BYTES,
                            label="workspace lockfile",
                            directory_descriptor=self._root_descriptor,
                        )
                    )
                except ValueError as exc:
                    raise CondaWorkspacesError(
                        "Workspace lockfile changed before publication."
                    ) from exc
                if (
                    current_content != self._lockfile_content
                    or current_generation != expected_lock_generation
                ):
                    raise CondaWorkspacesError(
                        "Workspace lockfile changed before publication."
                    )
            if self._root_descriptor is None:
                if path.is_symlink():
                    raise CondaWorkspacesError(
                        f"Workspace lockfile cannot be a symlink: {path}"
                    )
            else:
                try:
                    lock_stat = os.stat(
                        path.name,
                        dir_fd=self._root_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    lock_stat = None
                if lock_stat is not None and not stat.S_ISREG(lock_stat.st_mode):
                    raise CondaWorkspacesError(
                        f"Workspace lockfile is not a regular file: {path}"
                    )
                manifest_stat = os.stat(
                    self.manifest_path.name,
                    dir_fd=self._root_descriptor,
                    follow_symlinks=False,
                )
                if lock_stat is not None and (
                    lock_stat.st_dev,
                    lock_stat.st_ino,
                ) == (manifest_stat.st_dev, manifest_stat.st_ino):
                    raise CondaWorkspacesError(
                        "Workspace lockfile cannot be a hardlink alias of the manifest."
                    )
            self.publish_manifest()
            if self._root_descriptor is None:
                validate_lockfile_output(self.ctx, path)
            try:
                if self._root_descriptor is None:
                    atomic_write_text(
                        path,
                        content,
                        expected_generation=expected_lock_generation,
                    )
                else:
                    atomic_write_text_at(
                        self._root_descriptor,
                        path.name,
                        content,
                        display_path=path,
                        expected_generation=expected_lock_generation,
                    )
            except ValueError as exc:
                raise CondaWorkspacesError(
                    "Workspace lockfile changed before publication."
                ) from exc
            if self._root_descriptor is not None:
                if self._guard_identity_validator is not None:
                    self._guard_identity_validator()
            self.started = True
