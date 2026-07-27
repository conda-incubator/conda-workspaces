"""Archive creation and extraction for conda workspaces.

Provides functions for collecting workspace files, creating tar archives
(gzip or zstandard), extracting with path traversal protection, bundling
conda packages for offline use, and inspecting archive contents.
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib
import os
import posixpath
import shutil
import stat
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from io import BytesIO
from os.path import expanduser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from conda.base.context import context as conda_context
from conda_lockfiles.load_yaml import load_yaml

from .exceptions import (
    ArchiveError,
    ArchiveHashMismatchError,
    ArchivePathTraversalError,
    LockfileNotFoundError,
    LockfileStaleError,
    WorkspaceParseError,
)
from .lockfile import MAX_LOCKFILE_BYTES, load_lockfile_data
from .manifests import find_parser
from .manifests.base import ManifestParser
from .models import (
    LockfileStatus,
    has_url_credentials,
    has_url_credentials_in_data,
    redact_url_text,
)
from .paths import (
    anchored_directory,
    atomic_binary_writer,
    has_absolute_path_syntax,
    is_path_segment,
    output_paths_collide,
    parse_relative_posix_path,
    portable_path_key,
    read_regular_file_bytes,
    regular_file_generation,
    rename_noreplace,
    validate_directory_output,
    validate_file_output,
)
from .publication import WorkspacePublication

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any, BinaryIO, Final

    from .context import WorkspaceContext
    from .models import ArchiveConfig
    from .paths import FileGeneration as OutputFileGeneration
    from .receipts import ArchiveReceipt

ARCHIVE_SUFFIXES: tuple[str, ...] = (
    ".tar.zst",
    ".tar.zstd",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
)
"""Recognised archive filename suffixes, longest first."""

MANIFEST_FILENAMES = {"conda.toml", "pixi.toml", "pyproject.toml"}
"""Filenames recognised as workspace manifests inside an archive."""

CONDA_PACKAGE_SUFFIXES: tuple[str, ...] = (".conda", ".tar.bz2")
"""Recognised conda package archive suffixes."""

MAX_ARCHIVE_MEMBERS: Final = 100_000
"""Maximum number of members accepted from one workspace archive."""

MAX_ARCHIVE_PATH_DEPTH: Final = 256
"""Maximum number of path components accepted for one archive member."""

MAX_ARCHIVE_PATH_BYTES: Final = 4_096
"""Maximum UTF-8 byte length accepted for one archive member path."""

MAX_ARCHIVE_COMPONENTS: Final = 1_000_000
"""Maximum cumulative path components accepted across archive members."""

MAX_ARCHIVE_METADATA_BYTES: Final = 64 * 1024**2
"""Maximum combined expanded size of GNU and PAX metadata, in bytes."""

MAX_ARCHIVE_METADATA_HEADERS: Final = 128
"""Maximum consecutive GNU and PAX extension headers."""

MAX_ARCHIVE_METADATA_HEADERS_TOTAL: Final = 100_000
"""Maximum combined GNU and PAX extension headers in one archive."""

MAX_ARCHIVE_PAX_RECORDS: Final = 100_000
"""Maximum combined PAX key/value records."""

MAX_ARCHIVE_EXPANDED_BYTES: Final = 100 * 1024**3
"""Maximum combined expanded size of regular archive members, in bytes."""

MAX_ARCHIVE_RAW_BYTES: Final = 100 * 1024**3
"""Maximum byte size accepted for one compressed or uncompressed archive."""

FileGeneration = tuple[int, int, int, int, int, int]
"""Stable identity and mutation-sensitive metadata for one regular file."""

_CURRENT_ARCHIVE_OUTPUT_GENERATION = object()


@dataclass
class _ArchiveWriteLimits:
    """Track aggregate resource use before archive payloads are opened."""

    members: int = 0
    expanded_bytes: int = 0
    path_components: int = 0

    def reserve(self, member: tarfile.TarInfo) -> None:
        """Reserve the limits consumed by *member* before writing it."""
        if self.members >= MAX_ARCHIVE_MEMBERS:
            raise ArchiveError(
                f"Archive contains more than {MAX_ARCHIVE_MEMBERS:,} members."
            )
        expanded_bytes = validate_tar_member_limits(member, self.expanded_bytes)
        member_path = validate_tar_member(member)
        path_components = self.path_components + len(portable_path_key(member_path))
        if path_components > MAX_ARCHIVE_COMPONENTS:
            raise ArchiveError(
                "Archive member paths exceed the maximum cumulative component"
                f" count of {MAX_ARCHIVE_COMPONENTS:,}."
            )

        self.members += 1
        self.expanded_bytes = expanded_bytes
        self.path_components = path_components


ALLOWED_TAR_TYPES: frozenset[bytes] = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
        tarfile.SYMTYPE,
    }
)
"""Tar member types accepted during extraction."""

EXTENDED_TAR_TYPES: frozenset[bytes] = frozenset(
    {
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_LONGLINK,
        tarfile.XHDTYPE,
        tarfile.XGLTYPE,
        tarfile.SOLARIS_XHDTYPE,
    }
)
"""Tar extension headers consumed before a materialized member is returned."""


class _BoundedTarInfo(tarfile.TarInfo):
    """Reject oversized GNU and PAX metadata before tarfile reads its payload."""

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type == tarfile.GNUTYPE_SPARSE:
            raise ArchiveError("Sparse archive members are not supported.")
        if self.type in EXTENDED_TAR_TYPES:
            metadata_headers = getattr(archive, "_workspace_metadata_headers", 0) + 1
            if metadata_headers > MAX_ARCHIVE_METADATA_HEADERS:
                raise ArchiveError(
                    "Archive contains more than"
                    f" {MAX_ARCHIVE_METADATA_HEADERS:,} consecutive metadata"
                    " headers."
                )
            setattr(archive, "_workspace_metadata_headers", metadata_headers)
            total_metadata_headers = (
                getattr(archive, "_workspace_metadata_headers_total", 0) + 1
            )
            if total_metadata_headers > MAX_ARCHIVE_METADATA_HEADERS_TOTAL:
                raise ArchiveError(
                    "Archive contains more than"
                    f" {MAX_ARCHIVE_METADATA_HEADERS_TOTAL:,} total metadata"
                    " headers."
                )
            setattr(
                archive,
                "_workspace_metadata_headers_total",
                total_metadata_headers,
            )
        else:
            setattr(archive, "_workspace_metadata_headers", 0)
            if self.type not in ALLOWED_TAR_TYPES:
                raise ArchiveError(
                    f"Archive member '{self.name}' has an unsupported type."
                )
        if self.type in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            if self.size > MAX_ARCHIVE_PATH_BYTES:
                label = (
                    "path" if self.type == tarfile.GNUTYPE_LONGNAME else "link target"
                )
                raise ArchiveError(
                    f"Archive member {label} metadata exceeds the maximum length of"
                    f" {MAX_ARCHIVE_PATH_BYTES:,} bytes."
                )
        if self.type in (
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
        ):
            expanded = getattr(archive, "_workspace_metadata_bytes", 0) + self.size
            if self.size < 0 or expanded > MAX_ARCHIVE_METADATA_BYTES:
                raise ArchiveError(
                    "Archive metadata expands beyond the maximum size of"
                    f" {MAX_ARCHIVE_METADATA_BYTES:,} bytes."
                )
            setattr(archive, "_workspace_metadata_bytes", expanded)
        return super()._proc_member(archive)  # ty: ignore[unresolved-attribute]

    def _proc_pax(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        """Bound PAX record count before tarfile allocates decoded entries."""
        position = archive.fileobj.tell()
        padded_size = (
            (self.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE * tarfile.BLOCKSIZE
        )
        data = archive.fileobj.read(padded_size)
        records = 0
        offset = 0
        while offset < len(data) and data[offset] != 0:
            separator = data.find(b" ", offset, min(offset + 32, len(data)))
            if separator < 0:
                break
            try:
                length = int(data[offset:separator])
            except ValueError:
                break
            if length < 5 or offset + length > len(data):
                break
            records += 1
            if (
                getattr(archive, "_workspace_pax_records", 0) + records
                > MAX_ARCHIVE_PAX_RECORDS
            ):
                raise ArchiveError(
                    "Archive contains more than"
                    f" {MAX_ARCHIVE_PAX_RECORDS:,} PAX metadata records."
                )
            offset += length
        setattr(
            archive,
            "_workspace_pax_records",
            getattr(archive, "_workspace_pax_records", 0) + records,
        )
        archive.fileobj.seek(position)
        del data
        return super()._proc_pax(archive)  # ty: ignore[unresolved-attribute]

    def _reject_sparse(self, *_args: object) -> None:
        """Reject PAX sparse maps before tarfile parses their data payload."""
        raise ArchiveError("Sparse archive members are not supported.")

    _proc_gnusparse_00 = _reject_sparse
    _proc_gnusparse_01 = _reject_sparse
    _proc_gnusparse_10 = _reject_sparse


class _HashingReader:
    """Hash exactly the bytes consumed while a regular file enters a tar."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        """Read and hash up to *size* bytes from the underlying stream."""
        data = self.stream.read(size)
        self.digest.update(data)
        return data


class _HashingWriter:
    """Hash a sequential archive output stream while preserving its interface."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()
        self.position = stream.tell()
        self.sequential = True

    def write(self, data: bytes) -> int:
        """Write *data* and hash the bytes accepted by the output stream."""
        if self.stream.tell() != self.position:
            self.sequential = False
        written = self.stream.write(data)
        self.digest.update(data[:written])
        self.position += written
        return written

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)


BUILTIN_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".conda/envs",
        ".pixi",
        "__pycache__",
    }
)
"""Directories excluded from archives regardless of user configuration."""

BUILTIN_SENSITIVE_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".env",
    "*/.env",
    ".env.*",
    "*/.env.*",
    ".aws",
    "*/.aws",
    ".azure",
    "*/.azure",
    ".config/gcloud",
    "*/.config/gcloud",
    ".docker",
    "*/.docker",
    ".gnupg",
    "*/.gnupg",
    ".kube",
    "*/.kube",
    ".ssh",
    "*/.ssh",
    ".terraform",
    "*/.terraform",
    ".condarc",
    "*/.condarc",
    ".git-credentials",
    "*/.git-credentials",
    ".conda/workspace.lock",
    "*/.conda/workspace.lock",
    ".netrc",
    "*/.netrc",
    ".npmrc",
    "*/.npmrc",
    ".pypirc",
    "*/.pypirc",
    "id_dsa",
    "*/id_dsa",
    "id_ecdsa",
    "*/id_ecdsa",
    "id_ed25519",
    "*/id_ed25519",
    "id_rsa",
    "*/id_rsa",
    "kubeconfig",
    "*/kubeconfig",
    "*.kubeconfig",
    "*.key",
    "*.keystore",
    "*.jks",
    "*.p12",
    "*.pem",
    "*.pfx",
    "*.secret",
    "*.secrets",
    "*.tfstate",
    "*.tfstate.*",
    "secrets",
    "*/secrets",
    "secrets.*",
    "*/secrets.*",
)
"""Common credential material excluded from archives by default."""

BUILTIN_SENSITIVE_EXCLUDE_EXCEPTIONS: tuple[str, ...] = (
    ".env.dist",
    "*/.env.dist",
    ".env.example",
    "*/.env.example",
    ".env.sample",
    "*/.env.sample",
    ".env.template",
    "*/.env.template",
)
"""Documented dotenv examples that are safe to keep in archives."""


@dataclass(frozen=True)
class WorkspaceArchiveExtractResult:
    """Result returned by :meth:`WorkspaceArchive.extract`."""

    target: Path
    receipt_path: Path | None
    verified: bool
    info: dict[str, object]
    primed_packages: int = 0
    cache_priming_skipped: bool = False


@dataclass(frozen=True)
class WorkspaceArchiveInstallResult:
    """Result returned by :meth:`WorkspaceArchive.install`."""

    target: Path
    environment: str | None
    install_prefix: Path | None
    runtime_prefix: str | None
    receipt_path: Path | None
    verified: bool
    info: dict[str, object]
    return_code: int = 0
    primed_packages: int = 0
    cache_priming_skipped: bool = False
    prefix_reference_matches: tuple[Path, ...] = ()
    prefix_reference_matches_truncated: bool = False


@dataclass(frozen=True)
class ArchiveOutputCapture:
    """Digest and inode captured from an archive's temporary output stream."""

    sha256: str | None
    identity: tuple[int, int]
    size: int


@dataclass(frozen=True)
class WorkspaceArchive:
    """High-level API for creating, extracting, and installing archives."""

    path: Path
    receipt: bool | str | Path | None = None

    def __init__(self, path: str | Path, receipt: bool | str | Path | None = None):
        object.__setattr__(self, "path", Path(path).expanduser().absolute())
        object.__setattr__(self, "receipt", receipt)

    @classmethod
    def create(
        cls,
        *,
        workspace: str | Path | None = None,
        output: str | Path | None = None,
        lock: bool = False,
        bundle: bool = False,
        exclude: tuple[str, ...] = (),
        receipt: bool | str | Path | None = None,
        dry_run: bool = False,
    ) -> WorkspaceArchive:
        """Create an archive for *workspace* and return its handle.

        When *dry_run* is true, all inputs are resolved and validated without
        writing the lockfile, archive, receipt, or package cache.
        """
        from .context import WorkspaceContext
        from .lockfile import lockfile_path, render_lockfile
        from .manifests import detect_and_parse, detect_workspace_file
        from .models import ArchiveConfig

        source = Path(workspace or Path.cwd())
        if source.is_symlink():
            raise ArchiveError(
                "Cannot archive workspace manifest: symbolic links are not supported.",
                hints=[f"Replace {source} with a regular path before archiving."],
            )
        try:
            manifest_source = (
                detect_workspace_file(source, reject_symlinks=True)
                if source.is_dir()
                else source
            )
        except WorkspaceParseError as exc:
            if "symbolic links are not supported" not in exc.reason:
                raise
            raise ArchiveError(
                "Cannot archive workspace manifest: symbolic links are not supported.",
                hints=[f"Replace {exc.path} with a regular file before archiving."],
            ) from exc
        _, config = detect_and_parse(manifest_source)
        ctx = WorkspaceContext(config)
        publication = (
            None
            if dry_run
            else WorkspacePublication.from_current_manifest(ctx, "archive")
        )
        lock_path = lockfile_path(ctx)
        manifest_path = Path(config.manifest_path)
        if manifest_path.is_symlink():
            raise ArchiveError(
                "Cannot archive workspace manifest: symbolic links are not supported.",
                hints=[
                    f"Replace {manifest_path} with a regular file before archiving."
                ],
            )
        lock_source: Path | list[dict] = lock_path
        lock_data: object | None = None
        lock_content: str | None = None

        if lock:
            if output_paths_collide(lock_path, manifest_path):
                raise ArchiveError(
                    "The workspace lockfile cannot overwrite the workspace manifest."
                )
            if lock_path.is_symlink():
                raise ArchiveError(
                    "Cannot archive workspace lockfile: symbolic links are not"
                    " supported.",
                    hints=[
                        f"Replace {lock_path} with a regular file before archiving."
                    ],
                )
        elif lock_path.is_symlink():
            raise ArchiveError(
                "Cannot archive workspace lockfile: symbolic links are not supported.",
                hints=[f"Replace {lock_path} with a regular file before archiving."],
            )

        archive_config = ArchiveConfig(
            include=config.archive.include,
            exclude=config.archive.exclude + tuple(exclude),
            compression=config.archive.compression,
            compression_level=config.archive.compression_level,
        )
        requested_output_path = (
            cls.default_output_path(ctx, output).expanduser().absolute()
        )
        if requested_output_path.is_symlink():
            raise ArchiveError("The archive output cannot be a symbolic link.")
        archive = cls(requested_output_path, receipt=receipt)
        output_path = archive.path
        receipt_path = archive.receipt_path
        extra_files = (lock_path,) if lock else ()
        manifest_member = manifest_path.relative_to(ctx.root).as_posix()
        lock_member = lock_path.relative_to(ctx.root).as_posix()

        protected_paths = {
            "workspace manifest": manifest_path,
            "workspace lockfile": lock_path,
        }
        output_paths = {"archive output": output_path}
        if receipt_path is not None:
            output_paths["receipt output"] = receipt_path
        for output_label, candidate in output_paths.items():
            if candidate.is_symlink():
                raise ArchiveError(f"The {output_label} cannot be a symbolic link.")
            for protected_label, protected in protected_paths.items():
                if output_paths_collide(candidate, protected):
                    raise ArchiveError(
                        f"The {output_label} cannot overwrite the {protected_label}."
                    )

        archive_output_generation = regular_file_generation(output_path)
        if lock:
            validate_file_output(lock_path)
        if receipt_path is not None:
            receipt_output_generation = regular_file_generation(receipt_path)
        else:
            receipt_output_generation = None
        with open_tar_for_write(
            BytesIO(),
            detect_compression(output_path),
            archive_config.compression_level,
        ):
            pass

        if receipt_path is not None:
            archive.validate_receipt_inputs(
                root=ctx.root,
                output=output_path,
                archive_config=archive_config,
                manifest_path=manifest_path,
                lockfile_path=lock_path,
                receipt_path=receipt_path,
                extra_files=extra_files,
            )

        try:
            initial_root = ctx.root.lstat()
            if not stat.S_ISDIR(initial_root.st_mode):
                raise ArchiveError(
                    f"Workspace root is not a regular directory: {ctx.root}"
                )
            root_generation = file_generation(initial_root)
            archive_files = exclude_archive_output(
                collect_archive_files(
                    ctx.root,
                    archive_config,
                    extra_files=extra_files,
                ),
                output_path,
            )
            if file_generation(ctx.root.lstat()) != root_generation:
                raise ArchiveError(
                    "Workspace root changed while archive inputs were collected."
                )
        except OSError as exc:
            raise ArchiveError(
                "Workspace root changed while archive inputs were collected:"
                f" {ctx.root}"
            ) from exc

        bundle_packages = None
        bundle_hashes = None
        regular_member_hashes: dict[str, str] = {}
        publication_guard = (
            publication.guard(expected_root_generation=root_generation)
            if publication is not None
            else nullcontext()
        )
        output_existed = archive_output_generation is not None
        receipt_existed = receipt_output_generation is not None
        created_output_identity = None
        created_receipt_identity = None
        captured_archive_output: ArchiveOutputCapture | None = None

        def capture_archive_output(value: ArchiveOutputCapture) -> None:
            nonlocal captured_archive_output
            captured_archive_output = value

        def remove_failed_outputs() -> None:
            if not receipt_existed and created_receipt_identity is not None:
                assert receipt_path is not None
                cls.remove_created_output(
                    receipt_path,
                    created_receipt_identity,
                )
            if not output_existed and created_output_identity is not None:
                cls.remove_created_output(output_path, created_output_identity)

        @contextmanager
        def guarded_archive_outputs() -> Iterator[None]:
            try:
                with publication_guard:
                    yield
            except BaseException:
                remove_failed_outputs()
                raise

        with guarded_archive_outputs():
            if lock:
                from .resolver import resolve_all_environments

                resolved_envs = resolve_all_environments(config, ctx.platform)
                lock_content = render_lockfile(
                    ctx,
                    resolved_envs,
                    config=config,
                    dry_run=dry_run,
                )
                lock_data = load_lockfile_data(lock_content)
                lock_source = lock_data.get("packages", []) or []

            manifest_text = (
                publication.original_text
                if publication is not None
                else config._manifest_text
            )
            if manifest_text is None:
                manifest_text = ManifestParser.read_manifest_text(manifest_path)
            cls.validate_manifest_credentials(manifest_text)
            regular_member_hashes[manifest_member] = hashlib.sha256(
                manifest_text.encode("utf-8")
            ).hexdigest()

            if lock_path.is_symlink():
                raise ArchiveError(
                    "Cannot archive workspace lockfile: symbolic links are not"
                    " supported.",
                    hints=[
                        f"Replace {lock_path} with a regular file before archiving."
                    ],
                )
            if lock_content is not None:
                lock_bytes = lock_content.encode("utf-8")
            elif lock_path.is_file():
                if publication is not None:
                    lock_bytes = publication.read_lockfile_bytes()
                else:
                    try:
                        lock_bytes = read_regular_file_bytes(
                            lock_path,
                            maximum_bytes=MAX_LOCKFILE_BYTES,
                            label="workspace lockfile",
                        )
                    except ValueError as exc:
                        raise ArchiveError(
                            f"Cannot read workspace lockfile safely: {lock_path}"
                        ) from exc
                lock_data = load_lockfile_data(lock_bytes)
                lock_source = lock_data.get("packages", []) or []
            else:
                lock_bytes = None

            if lock_data is not None:
                assert lock_bytes is not None
                cls.validate_lockfile_credentials(
                    lock_data,
                    lock_bytes.decode("utf-8"),
                )
            if lock_bytes is not None:
                regular_member_hashes[lock_member] = hashlib.sha256(
                    lock_bytes
                ).hexdigest()

            if receipt_path is not None:
                if lock_data is None:
                    raise ArchiveError(
                        "Cannot create an archive receipt without conda.lock."
                    )
                from .receipts import ReceiptInventory

                ReceiptInventory.from_lockfile_data(
                    lock_data,
                    environment_prefixes=receipt_environment_prefixes(
                        config_environments=list(config.environments),
                        ctx_root=ctx.root,
                        env_prefix=ctx.env_prefix,
                    ),
                )

            if bundle:
                if not lock and not lock_path.is_file():
                    raise ArchiveError(
                        "Cannot bundle packages: no conda.lock found.",
                        hints=["Run 'conda workspace lock' first."],
                    )
                cache_dirs = [Path(d) for d in conda_context.pkgs_dirs]
                bundle_packages = collect_bundle_packages(lock_source, cache_dirs)
                for package in bundle_packages:
                    if package.is_symlink() or not package.is_file():
                        raise ArchiveError(
                            f"Cannot bundle package: {package} is not a regular file."
                        )
                    for output_label, candidate in output_paths.items():
                        if output_paths_collide(candidate, package):
                            raise ArchiveError(
                                f"The {output_label} cannot overwrite a bundled"
                                " package."
                            )
                verify_package_hashes(bundle_packages, lock_source)
                bundle_hashes = build_hash_index(lock_source)

            try:
                if lock_content is not None and not dry_run:
                    cast("WorkspacePublication", publication).publish_lockfile(
                        lock_content
                    )
                archive_path = create_archive(
                    ctx.root,
                    output_path,
                    archive_config,
                    files=archive_files,
                    root_descriptor=(
                        publication.guarded_root_descriptor
                        if publication is not None
                        else None
                    ),
                    bundle_packages=bundle_packages,
                    bundle_hashes=bundle_hashes,
                    extra_files=extra_files,
                    regular_members=(
                        manifest_member,
                        lock_member,
                    ),
                    regular_member_hashes=regular_member_hashes,
                    virtual_regular_members=(
                        (lock_member,) if lock and not lock_path.exists() else ()
                    ),
                    capture_output=capture_archive_output,
                    capture_sha256=receipt_path is not None,
                    expected_output_generation=archive_output_generation,
                    dry_run=dry_run,
                )
                if not dry_run:
                    assert captured_archive_output is not None
                    created_output = archive_path.lstat()
                    if (
                        not stat.S_ISREG(created_output.st_mode)
                        or (created_output.st_dev, created_output.st_ino)
                        != captured_archive_output.identity
                        or created_output.st_size != captured_archive_output.size
                    ):
                        raise ArchiveError(
                            "Archive output changed after it was published:"
                            f" {archive_path}"
                        )
                    created_output_identity = captured_archive_output.identity
                if receipt_path is not None and not dry_run:
                    assert lock_data is not None
                    assert captured_archive_output is not None
                    assert captured_archive_output.sha256 is not None
                    live_archive_sha256, archive_generation = (
                        file_sha256_with_generation(
                            archive_path,
                            expected_identity=captured_archive_output.identity,
                            maximum_bytes=MAX_ARCHIVE_RAW_BYTES,
                            label="Archive output",
                        )
                    )
                    if live_archive_sha256 != captured_archive_output.sha256:
                        raise ArchiveHashMismatchError(
                            archive_path.name,
                            expected=captured_archive_output.sha256,
                            actual=live_archive_sha256,
                        )
                    receipt_obj = cls.build_receipt(
                        ctx=ctx,
                        archive_config=archive_config,
                        archive_name=archive_path.name,
                        archive_sha256=captured_archive_output.sha256,
                        manifest_name=manifest_member,
                        manifest_sha256=regular_member_hashes[manifest_member],
                        lockfile_name=lock_member,
                        lockfile_sha256=regular_member_hashes[lock_member],
                        lockfile_data=lock_data,
                        options={
                            "bundle": bundle,
                            "lock": lock,
                            "include": list(archive_config.include),
                            "exclude": list(archive_config.exclude),
                            "compressionLevel": archive_config.compression_level,
                        },
                    )
                    require_regular_file_generation(
                        archive_path,
                        archive_generation,
                        label="Archive output",
                    )
                    expected_receipt_sha256 = hashlib.sha256(
                        receipt_obj.serialized_text().encode("utf-8")
                    ).hexdigest()
                    receipt_obj.write(
                        receipt_path,
                        expected_generation=receipt_output_generation,
                    )
                    receipt_sha256, receipt_generation = file_sha256_with_generation(
                        receipt_path,
                        label="Receipt output",
                    )
                    if receipt_sha256 != expected_receipt_sha256:
                        raise ArchiveHashMismatchError(
                            receipt_path.name,
                            expected=expected_receipt_sha256,
                            actual=receipt_sha256,
                        )
                    created_receipt_identity = receipt_generation[:2]
                    require_regular_file_generation(
                        archive_path,
                        archive_generation,
                        label="Archive output",
                    )
            except BaseException:
                remove_failed_outputs()
                raise

        return cls(archive_path, receipt=receipt_path)

    @staticmethod
    def default_output_path(ctx: WorkspaceContext, output: str | Path | None) -> Path:
        """Return the explicit or workspace-name-derived output path."""
        if output is not None:
            return Path(output)

        name = ctx.config.name or ctx.root.name
        if not is_path_segment(name):
            raise ArchiveError(
                "Workspace name cannot be used as a default archive filename.",
                hints=[
                    "Use a simple workspace name without path separators,",
                    "or pass -o/--output to choose the archive path explicitly.",
                ],
            )
        ext = {"zst": ".tar.zst", "gz": ".tar.gz", "bz2": ".tar.bz2"}.get(
            ctx.config.archive.compression,
            ".tar.zst",
        )
        return ctx.root / f"{name}{ext}"

    @staticmethod
    def validate_manifest_credentials(content: str) -> None:
        """Reject credential-bearing strings before copying a manifest."""
        document = ManifestParser.parse_toml_text(content).unwrap()
        if has_url_credentials_in_data(document) or has_url_credentials(content):
            raise ArchiveError(
                "Cannot archive credentials embedded in the workspace manifest.",
                hints=[
                    (
                        "Remove and rotate the credential, then configure"
                        " authentication through Conda outside the repository."
                    )
                ],
            )

    @staticmethod
    def validate_lockfile_credentials(data: dict[str, object], content: str) -> None:
        """Reject a lockfile whose serialized URLs require redaction."""
        if has_url_credentials_in_data(data) or has_url_credentials(content):
            raise ArchiveError(
                "Cannot archive credentials embedded in conda.lock.",
                hints=["Remove and rotate the credential, then regenerate conda.lock."],
            )

    @staticmethod
    def validate_receipt_inputs(
        *,
        root: Path,
        output: Path,
        archive_config: ArchiveConfig,
        manifest_path: Path,
        lockfile_path: Path,
        receipt_path: Path,
        extra_files: tuple[Path, ...] = (),
    ) -> None:
        """Validate inputs required to write a receipt for a new archive."""
        if output_paths_collide(receipt_path, output):
            raise ArchiveError(
                "Receipt path cannot be the archive path.",
                hints=["Choose a separate JSON path for --receipt."],
            )
        if not manifest_path.is_file():
            raise ArchiveError(
                "Cannot write receipt: workspace manifest was not found."
            )
        if not lockfile_path.is_file() and lockfile_path not in extra_files:
            raise ArchiveError(
                "Cannot write receipt: no conda.lock found.",
                hints=["Run 'conda workspace lock' first."],
            )

        from .receipts import ArchiveReceipt

        subject_names = (
            output.name,
            ArchiveReceipt.archive_name(root, manifest_path),
            ArchiveReceipt.archive_name(root, lockfile_path),
        )
        if len(set(subject_names)) != len(subject_names):
            raise ArchiveError(
                "Cannot write receipt: archive subjects would have duplicate names."
            )

        archive_files = collect_archive_files(
            root,
            archive_config,
            extra_files=extra_files,
        )
        receipt_identity = receipt_path.resolve(strict=False)
        archive_files = [
            path
            for path in archive_files
            if path.resolve(strict=False) != receipt_identity
        ]
        if any(output_paths_collide(receipt_path, path) for path in archive_files):
            raise ArchiveError(
                "Receipt output cannot overwrite an archived workspace input."
            )
        archive_members = {
            path.relative_to(root).as_posix()
            for path in archive_files
            if path.resolve() != output.resolve()
        }
        required_members: dict[str, Path] = {
            "workspace manifest": manifest_path,
            "workspace lockfile": lockfile_path,
        }
        missing = []
        for label, path in required_members.items():
            try:
                archive_name = path.relative_to(root).as_posix()
            except ValueError:
                missing.append(label)
                continue
            if archive_name not in archive_members:
                missing.append(f"{label} ({archive_name})")
        if missing:
            raise ArchiveError(
                f"Cannot write receipt: archive would not include {missing[0]}.",
                hints=[
                    (
                        "Receipt verification requires the workspace manifest and"
                        " conda.lock to be included in the archive."
                    ),
                    "Remove matching include/exclude filters or run without --receipt.",
                ],
            )

    @staticmethod
    def build_receipt(
        *,
        ctx: WorkspaceContext,
        archive_config: ArchiveConfig,
        archive_name: str,
        archive_sha256: str,
        manifest_name: str,
        manifest_sha256: str,
        lockfile_name: str,
        lockfile_sha256: str,
        lockfile_data: object,
        options: dict[str, object],
    ) -> ArchiveReceipt:
        """Build the external receipt from captured archive inputs."""
        from .receipts import ArchiveReceipt

        return ArchiveReceipt.build_from_captured(
            archive_name=archive_name,
            archive_sha256=archive_sha256,
            manifest_name=manifest_name,
            manifest_sha256=manifest_sha256,
            lockfile_name=lockfile_name,
            lockfile_sha256=lockfile_sha256,
            lockfile_data=lockfile_data,
            archive_config=archive_config,
            environment_prefixes=receipt_environment_prefixes(
                config_environments=list(ctx.config.environments),
                ctx_root=ctx.root,
                env_prefix=ctx.env_prefix,
            ),
            options=options,
        )

    @property
    def receipt_path(self) -> Path | None:
        """Return the configured external receipt path, if any."""
        return resolve_receipt_path(self.path, self.receipt)

    def default_target(self, cwd: str | Path | None = None) -> Path:
        """Return the default extraction target derived from the archive name."""
        stem = self.path.name
        for suffix in ARCHIVE_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        return Path.cwd() / stem if cwd is None else Path(cwd) / stem

    def inspect(self) -> dict[str, object]:
        """Return archive metadata without extracting it."""
        archive_path = self.require_existing_archive()
        return inspect_archive(archive_path)

    def verify(self) -> ArchiveReceipt:
        """Verify the archive against its external receipt."""
        receipt_path = self.receipt_path
        if receipt_path is None:
            raise ArchiveError("--receipt is required to verify an archive.")
        from .receipts import ArchiveReceipt

        receipt = ArchiveReceipt.load(receipt_path)
        receipt.verify_archive(self.require_existing_archive())
        return receipt

    def extract(
        self,
        *,
        target: str | Path | None = None,
        require_sha256: bool = False,
        prime_cache: bool = True,
        package_cache: str | Path | None = None,
        dry_run: bool = False,
    ) -> WorkspaceArchiveExtractResult:
        """Extract the archive and optionally prime bundled package cache files."""
        return self._extract(
            target=target,
            require_sha256=require_sha256,
            prime_cache=prime_cache,
            package_cache=package_cache,
            dry_run=dry_run,
        )

    def _extract(
        self,
        *,
        target: str | Path | None,
        require_sha256: bool,
        prime_cache: bool,
        package_cache: str | Path | None,
        dry_run: bool,
        validate_workspace: Callable[[Path, ArchiveReceipt | None], None] | None = None,
    ) -> WorkspaceArchiveExtractResult:
        """Stage, validate, and optionally promote an archive workspace."""
        requested_target = (
            Path(target).expanduser() if target is not None else self.default_target()
        )
        target_path = requested_target.resolve()
        ensure_extract_target_empty(requested_target)
        validate_directory_output(requested_target)
        if target_path.exists():
            raise ArchiveError(
                "Archive extraction requires a target that does not already exist.",
                hints=["Remove the empty target directory before extracting."],
            )

        if require_sha256 and self.receipt_path is None:
            raise ArchiveError("--require-sha256 requires --receipt.")
        archive_path = self.require_existing_archive()
        receipt = None
        if self.receipt_path is not None:
            from .receipts import ArchiveReceipt

            receipt = ArchiveReceipt.load(self.receipt_path)

        temporary_parent = None
        if not dry_run:
            temporary_parent = target_path.parent
            while not temporary_parent.exists():
                temporary_parent = temporary_parent.parent
        with tempfile.TemporaryDirectory(
            prefix="conda-workspaces-",
            dir=temporary_parent,
        ) as temporary:
            snapshot_path = Path(temporary) / archive_path.name
            self.snapshot_archive(archive_path, snapshot_path)
            if receipt is not None:
                receipt.verify_archive(snapshot_path)
            info = inspect_archive(snapshot_path)
            if not info["has_manifest"]:
                raise ArchiveError(
                    "Not a workspace archive: no manifest found.",
                    hints=["This does not appear to be a conda workspace archive."],
                )
            if receipt is not None:
                with open_tar(snapshot_path) as tar:
                    for label, name in zip(
                        ("workspace manifest", "workspace lockfile"),
                        receipt.workspace_paths,
                        strict=True,
                    ):
                        try:
                            member = tar.getmember(name)
                        except KeyError:
                            raise ArchiveError(
                                f"Receipt {label} is missing from the archive."
                            ) from None
                        if not member.isreg():
                            raise ArchiveError(
                                f"Receipt {label} is not a regular archive member."
                            )

            staged = extract_archive(snapshot_path, Path(temporary) / "workspace")
            if receipt is not None:
                receipt.verify_extracted(staged, require_sha256=require_sha256)
            cache_priming_skipped = bool(
                info["has_packages"] and prime_cache and receipt is None
            )
            cache_plan: list[tuple[str, Path, str]] = []
            if info["has_packages"] and prime_cache and receipt is not None:
                if package_cache is None:
                    cache_path = Path(conda_context.pkgs_dirs[0])
                else:
                    cache_path = Path(package_cache)
                if cache_path.resolve().is_relative_to(target_path):
                    raise ArchiveError(
                        "Package cache must be outside the archive extraction target.",
                        hints=["Choose a package cache in a separate directory."],
                    )
                validate_directory_output(cache_path)

                packages = sorted(
                    path
                    for suffix in CONDA_PACKAGE_SUFFIXES
                    for path in (staged / "packages").glob(f"*{suffix}")
                )
                verify_package_hashes(packages, staged / "conda.lock")
                receipt_hashes: dict[str, str] = {}
                for environment in receipt.inventory.data:
                    records = environment.get("packages")
                    if not isinstance(records, list):
                        raise ArchiveError("Invalid receipt package inventory.")
                    for item in records:
                        if not isinstance(item, dict):
                            raise ArchiveError("Invalid receipt package inventory.")
                        record = cast("dict[str, object]", item)
                        filename = record.get("fn")
                        digest = record.get("sha256")
                        if not isinstance(filename, str) or not isinstance(digest, str):
                            continue
                        previous = receipt_hashes.setdefault(filename, digest)
                        if previous != digest:
                            raise ArchiveError(
                                "Receipt contains conflicting package hashes for"
                                f" '{filename}'."
                            )
                for package in packages:
                    destination = cache_path / package.name
                    if destination.is_symlink():
                        raise ArchiveError(
                            "Package cache destinations cannot be symbolic links."
                        )
                    validate_file_output(destination)
                    expected = receipt_hashes.get(package.name)
                    if expected is None:
                        raise ArchiveError(
                            f"Receipt lacks a SHA256 digest for '{package.name}'."
                        )
                    if destination.exists():
                        actual = file_sha256(destination)
                        if actual != expected:
                            raise ArchiveHashMismatchError(
                                package.name,
                                expected=expected,
                                actual=actual,
                            )
                        continue
                    cache_plan.append((package.name, destination, expected))

            if validate_workspace is not None:
                validate_workspace(staged, receipt)

            primed_packages = len(cache_plan)
            if dry_run:
                extracted = target_path
            else:
                for name, destination, expected in cache_plan:
                    source = staged / "packages" / name
                    try:
                        with atomic_binary_writer(
                            destination,
                            expected_identity=None,
                        ) as output_stream:
                            with open_stable_regular_file(
                                source,
                                label="Bundled package source",
                            ) as input_stream:
                                hashing_reader = _HashingReader(input_stream)
                                shutil.copyfileobj(
                                    hashing_reader,
                                    output_stream,
                                    length=1024 * 1024,
                                )
                                actual = hashing_reader.digest.hexdigest()
                                if actual != expected:
                                    raise ArchiveHashMismatchError(
                                        name,
                                        expected=expected,
                                        actual=actual,
                                    )
                    except FileExistsError as exc:
                        raise ArchiveError(
                            "Package cache destination changed before publication:"
                            f" {destination}"
                        ) from exc
                    except (OSError, ValueError) as exc:
                        raise ArchiveError(
                            "Package cache destination cannot be published safely:"
                            f" {destination}"
                        ) from exc

                staged_generation = staged.lstat()
                with (
                    anchored_directory(
                        target_path.parent,
                        create=True,
                    ) as parent_descriptor,
                    anchored_directory(staged.parent) as source_descriptor,
                ):
                    if requested_target.resolve() != target_path:
                        raise ArchiveError(
                            "Extraction target changed while the archive was staged."
                        )
                    if source_descriptor is None or parent_descriptor is None:
                        if target_path.exists() or target_path.is_symlink():
                            raise ArchiveError(
                                "Extraction target changed while the archive was"
                                " staged."
                            )
                        rename_noreplace(staged, target_path)
                        published = target_path.lstat()
                    else:
                        opened_parent = os.fstat(parent_descriptor)
                        live_parent = target_path.parent.lstat()
                        if not stat.S_ISDIR(live_parent.st_mode) or (
                            live_parent.st_dev,
                            live_parent.st_ino,
                        ) != (opened_parent.st_dev, opened_parent.st_ino):
                            raise ArchiveError(
                                "Extraction target changed while the archive was"
                                " staged."
                            )
                        try:
                            rename_noreplace(
                                staged.name,
                                target_path.name,
                                source_dir_fd=source_descriptor,
                                destination_dir_fd=parent_descriptor,
                            )
                        except OSError as exc:
                            raise ArchiveError(
                                "Extraction target changed while the archive was"
                                " staged."
                            ) from exc
                        published = os.stat(
                            target_path.name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                        live_parent = target_path.parent.lstat()
                        if not stat.S_ISDIR(live_parent.st_mode) or (
                            live_parent.st_dev,
                            live_parent.st_ino,
                        ) != (opened_parent.st_dev, opened_parent.st_ino):
                            raise ArchiveError(
                                "Extraction target changed while the archive was"
                                " staged."
                            )
                    live_target = target_path.lstat()
                    expected_identity = (
                        staged_generation.st_dev,
                        staged_generation.st_ino,
                    )
                    if (
                        requested_target.resolve() != target_path
                        or not stat.S_ISDIR(published.st_mode)
                        or (published.st_dev, published.st_ino) != expected_identity
                        or not stat.S_ISDIR(live_target.st_mode)
                        or (live_target.st_dev, live_target.st_ino) != expected_identity
                    ):
                        raise ArchiveError(
                            "Extraction target changed while the archive was staged."
                        )
                extracted = target_path

            return WorkspaceArchiveExtractResult(
                target=extracted,
                receipt_path=self.receipt_path,
                verified=receipt is not None,
                info=info,
                primed_packages=primed_packages,
                cache_priming_skipped=cache_priming_skipped,
            )

    def install(
        self,
        *,
        target: str | Path | None = None,
        environment: str | None = None,
        prefix: str | Path | None = None,
        dest: str | Path | None = None,
        require_sha256: bool = False,
        prime_cache: bool = True,
        package_cache: str | Path | None = None,
        install_handler: Callable[[Path, str | None, Path | None, str | None], int]
        | None = None,
        dry_run: bool = False,
    ) -> WorkspaceArchiveInstallResult:
        """Extract the archive and install environments from its lockfile.

        When *dry_run* is true, archive and install inputs are validated
        without extracting files or changing an environment prefix.
        """
        final_prefix = str(prefix) if prefix is not None else None
        if final_prefix is not None and not environment:
            raise ArchiveError(
                "--prefix requires an explicit environment.",
                hints=["Pass -e/--environment with --prefix."],
            )
        if dest is not None and final_prefix is None:
            raise ArchiveError(
                "--dest requires --prefix.",
                hints=[
                    (
                        "Pass --prefix to declare the final runtime prefix for"
                        " the selected environment."
                    ),
                ],
            )
        if final_prefix is not None:
            final_prefix = expanduser(final_prefix)
            if not is_absolute_runtime_prefix(final_prefix):
                raise ArchiveError(
                    "--prefix must be an absolute path.",
                    hints=["Pass an absolute runtime prefix such as /opt/runtime."],
                )
            if dest is None and not Path(final_prefix).is_absolute():
                raise ArchiveError(
                    "--prefix must use the host platform's absolute path syntax.",
                    hints=[
                        (
                            "Pass --dest when staging an archive for a different"
                            " operating system."
                        ),
                    ],
                )

        install_prefix = Path(final_prefix) if final_prefix is not None else None
        runtime_prefix = None
        if final_prefix is not None:
            if dest is not None:
                dest_path = Path(dest).expanduser().absolute()
                if dest_path.is_symlink():
                    raise ArchiveError("--dest cannot be a symbolic link.")
                validate_directory_output(dest_path)
                try:
                    relative_prefix = runtime_prefix_relative_path(final_prefix)
                except ValueError as exc:
                    raise ArchiveError(
                        "--prefix must not contain '.' or '..' path components."
                    ) from exc
                install_prefix = dest_path / relative_prefix
                runtime_prefix = final_prefix
            elif str(install_prefix) != final_prefix:
                runtime_prefix = final_prefix

        if install_prefix is not None:
            validate_directory_output(install_prefix)

        def validate_workspace(
            workspace: Path,
            receipt: ArchiveReceipt | None,
        ) -> None:
            from .context import WorkspaceContext
            from .lockfile import install_from_lockfile, lockfile_status
            from .manifests import detect_and_parse

            manifest_path = self.resolve_extracted_manifest(workspace)
            lock_path = workspace / "conda.lock"
            if lock_path.is_symlink() or not lock_path.is_file():
                raise ArchiveError(
                    "Cannot install from archive: no conda.lock found.",
                    hints=["Create the archive with a conda.lock file."],
                )
            if receipt is not None and receipt.workspace_paths != (
                manifest_path.name,
                "conda.lock",
            ):
                raise ArchiveError(
                    "Cannot install from archive: the receipt must bind the selected"
                    " root manifest and conda.lock."
                )

            load_yaml.cache_clear()
            _, config = detect_and_parse(manifest_path)
            ctx = WorkspaceContext(config)
            try:
                status = lockfile_status(ctx, config)
            except Exception as exc:
                raise ArchiveError(
                    "Cannot parse workspace lockfile: conda.lock"
                ) from exc
            if status.status == LockfileStatus.OUT_OF_DATE:
                raise LockfileStaleError(
                    manifest_path,
                    lock_path,
                    reason=status.reason,
                )
            if status.status == LockfileStatus.MISSING:
                raise LockfileNotFoundError("(all)", lock_path)

            names = (
                [environment] if environment is not None else list(config.environments)
            )
            for name in names:
                config.get_environment(name)
                install_from_lockfile(
                    ctx,
                    name,
                    prefix=install_prefix if environment is not None else None,
                    target_prefix_override=(
                        runtime_prefix if environment is not None else None
                    ),
                    dry_run=True,
                )

        extract_result = self._extract(
            target=target,
            require_sha256=require_sha256,
            prime_cache=prime_cache,
            package_cache=package_cache,
            dry_run=dry_run,
            validate_workspace=validate_workspace,
        )

        if dry_run:
            return_code = 0
        else:
            handler = install_handler or self.install_from_lockfile
            return_code = handler(
                extract_result.target,
                environment,
                install_prefix,
                runtime_prefix,
            )

        prefix_matches: tuple[Path, ...] = ()
        prefix_matches_truncated = False
        if (
            return_code == 0
            and not dry_run
            and install_prefix is not None
            and runtime_prefix is not None
        ):
            matches, prefix_matches_truncated = scan_prefix_references(
                install_prefix,
                install_prefix,
            )
            prefix_matches = tuple(matches)

        return WorkspaceArchiveInstallResult(
            target=extract_result.target,
            environment=environment,
            install_prefix=install_prefix,
            runtime_prefix=runtime_prefix,
            receipt_path=extract_result.receipt_path,
            verified=extract_result.verified,
            info=extract_result.info,
            return_code=return_code,
            primed_packages=extract_result.primed_packages,
            cache_priming_skipped=extract_result.cache_priming_skipped,
            prefix_reference_matches=prefix_matches,
            prefix_reference_matches_truncated=prefix_matches_truncated,
        )

    @staticmethod
    def resolve_extracted_manifest(workspace: Path) -> Path:
        """Return the sole valid workspace manifest at an extracted archive root."""
        candidates = []
        for filename in MANIFEST_FILENAMES:
            path = workspace / filename
            if path.is_symlink() or not path.is_file():
                continue
            try:
                find_parser(path).parse(path)
            except WorkspaceParseError:
                continue
            else:
                candidates.append(path)

        if not candidates:
            raise ArchiveError(
                "Cannot install from archive: no valid workspace manifest"
                " was found at the archive root."
            )
        if len(candidates) > 1:
            raise ArchiveError(
                "Cannot install from archive: multiple workspace manifests"
                " were found at the archive root.",
                hints=[
                    (
                        "Keep only the selected manifest in the archive before using"
                        " --install."
                    ),
                ],
            )
        return candidates[0]

    @staticmethod
    def install_from_lockfile(
        workspace: Path,
        environment: str | None,
        prefix: Path | None,
        target_prefix_override: str | None,
    ) -> int:
        """Install workspace environments from ``conda.lock`` without the CLI."""
        from .context import WorkspaceContext
        from .lockfile import install_from_lockfile
        from .manifests import detect_and_parse

        load_yaml.cache_clear()

        _, config = detect_and_parse(
            WorkspaceArchive.resolve_extracted_manifest(workspace)
        )
        ctx = WorkspaceContext(config)
        if environment is not None:
            install_from_lockfile(
                ctx,
                environment,
                prefix=prefix,
                target_prefix_override=target_prefix_override,
            )
            return 0

        for name in config.environments:
            install_from_lockfile(ctx, name)
        return 0

    def require_existing_archive(self) -> Path:
        """Return *path* after verifying that it points to an archive file."""
        if not self.path.is_file():
            raise ArchiveError(f"Archive not found: {self.path}")
        return self.path

    @staticmethod
    def snapshot_archive(source: Path, destination: Path) -> None:
        """Copy one no-follow archive generation into private staging."""
        try:
            with atomic_binary_writer(
                destination,
                expected_identity=None,
            ) as output_stream:
                with open_stable_regular_file(
                    source,
                    label="Archive",
                    maximum_bytes=MAX_ARCHIVE_RAW_BYTES,
                ) as input_stream:
                    shutil.copyfileobj(
                        input_stream,
                        output_stream,
                        length=1024 * 1024,
                    )
        except (OSError, ValueError) as exc:
            raise ArchiveError(
                f"Archive cannot be snapshotted safely: {source}"
            ) from exc

    @staticmethod
    def remove_created_output(path: Path, expected: tuple[int, int]) -> None:
        """Remove *path* only while it still names the created output."""
        try:
            with anchored_directory(path.parent) as parent_descriptor:
                if parent_descriptor is None:
                    current = path.lstat()
                    if (current.st_dev, current.st_ino) == expected:
                        path.unlink()
                    return
                current = os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (current.st_dev, current.st_ino) == expected:
                    os.unlink(path.name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            return


def is_absolute_runtime_prefix(prefix: str) -> bool:
    """Return whether *prefix* is absolute as a POSIX or Windows path."""
    return has_absolute_path_syntax(prefix)


def runtime_prefix_relative_path(prefix: str) -> Path:
    """Return *prefix* relative to its root using host path separators."""
    posix_prefix = PurePosixPath(prefix)
    if posix_prefix.is_absolute():
        parts = posix_prefix.relative_to(posix_prefix.anchor).parts
    else:
        windows_prefix = PureWindowsPath(prefix)
        if not windows_prefix.is_absolute():
            raise ValueError(f"Runtime prefix is not absolute: {prefix!r}")
        parts = windows_prefix.relative_to(windows_prefix.anchor).parts

    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"Runtime prefix contains traversal: {prefix!r}")
    return Path(*parts)


def file_contains_bytes(
    path: Path, needle: bytes, *, chunk_size: int = 1024 * 1024
) -> bool:
    """Return whether *path* contains *needle* without loading it all at once."""
    if not needle:
        return False

    overlap = b""
    try:
        with open_stable_regular_file(path, label="Scanned file") as fh:
            while chunk := fh.read(chunk_size):
                data = overlap + chunk
                if needle in data:
                    return True
                overlap = data[-(len(needle) - 1) :] if len(needle) > 1 else b""
    except (OSError, ArchiveError):
        return False
    return False


def scan_prefix_references(
    root: Path,
    prefix: Path,
    *,
    limit: int = 10,
) -> tuple[list[Path], bool]:
    """Find files below *root* that still contain *prefix* as bytes."""
    if not root.is_dir():
        return [], False

    needle = str(prefix).encode()
    matches: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if file_contains_bytes(path, needle):
            matches.append(path)
            if len(matches) > limit:
                return matches[:limit], True
    return matches, False


def resolve_receipt_path(archive_path: Path, receipt: object) -> Path | None:
    """Resolve an optional ``--receipt [PATH]`` style value."""
    if receipt in (None, False):
        return None
    if receipt is True:
        from .receipts import ArchiveReceipt

        return ArchiveReceipt.default_path(archive_path)
    if isinstance(receipt, Path):
        return receipt
    if isinstance(receipt, str):
        return Path(receipt)
    raise ArchiveError("Invalid --receipt value.")


def receipt_environment_prefixes(
    *,
    config_environments: list[str],
    ctx_root: Path,
    env_prefix: Callable[[str], Path],
) -> dict[str, str]:
    """Return environment prefixes to record in a receipt predicate."""
    prefixes: dict[str, str] = {}
    for name in config_environments:
        prefix = env_prefix(name)
        try:
            prefixes[name] = prefix.relative_to(ctx_root).as_posix()
        except ValueError:
            prefixes[name] = prefix.as_posix()
    return prefixes


def parse_relative_archive_path(
    path: str,
    *,
    allow_parent: bool = False,
) -> PurePosixPath:
    """Return *path* as a validated POSIX archive path.

    Tar members and receipt paths use POSIX separators regardless of the
    host OS.  Keeping this policy in one helper lets extraction and receipt
    verification reject the same ambiguous path syntax while raising their
    own domain-specific errors.
    """
    try:
        return parse_relative_posix_path(
            path,
            allow_parent=allow_parent,
            require_canonical=True,
        )
    except ValueError as exc:
        raise ValueError(f"Invalid relative archive path: {path!r}") from exc


def is_git_repo(root: Path) -> bool:
    """Return True if *root* is inside a git working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except FileNotFoundError:
        return False


def git_tracked_files(root: Path) -> list[Path]:
    """Return absolute paths for all git-tracked files under *root*."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = []
    for entry in result.stdout.split("\0"):
        if entry:
            full = root / entry
            if full.is_file():
                paths.append(full)
    return paths


def is_excluded_by_builtins(rel_path: str) -> bool:
    """Return True if *rel_path* falls under a builtin-excluded directory."""
    normalized_path = "/".join(portable_path_key(PurePosixPath(rel_path)))
    for excl in BUILTIN_EXCLUDE_DIRS:
        normalized_exclusion = excl.casefold()
        if normalized_path == normalized_exclusion or normalized_path.startswith(
            normalized_exclusion + "/"
        ):
            return True
    normalized_exceptions = tuple(
        pattern.casefold() for pattern in BUILTIN_SENSITIVE_EXCLUDE_EXCEPTIONS
    )
    if matches_patterns(normalized_path, normalized_exceptions):
        return False
    normalized_patterns = tuple(
        pattern.casefold() for pattern in BUILTIN_SENSITIVE_EXCLUDE_PATTERNS
    )
    return matches_patterns(normalized_path, normalized_patterns)


def matches_patterns(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """Return True if *rel_path* or any parent matches one glob pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        parts = rel_path.split("/")
        for i in range(len(parts)):
            partial = "/".join(parts[: i + 1])
            if fnmatch.fnmatch(partial, pattern):
                return True
    return False


def collect_archive_files(
    root: Path,
    archive_config: ArchiveConfig,
    *,
    extra_files: tuple[Path, ...] = (),
) -> list[Path]:
    """Collect workspace files eligible for archiving.

    In git repos, only tracked files are included. Otherwise all files
    under *root* are considered. *extra_files* are also considered even
    when they are untracked or not written yet. Builtin and user filters
    apply to every candidate.
    """
    if is_git_repo(root):
        candidates = git_tracked_files(root)
    else:
        candidates = [p for p in root.rglob("*") if p.is_file()]
    candidates.extend(extra_files)

    result: list[Path] = []
    for path in set(candidates):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_excluded_by_builtins(rel):
            continue
        if archive_config.include and not matches_patterns(rel, archive_config.include):
            continue
        if matches_patterns(rel, archive_config.exclude):
            continue
        result.append(path)

    return sorted(result)


def detect_compression(output: Path) -> str:
    """Infer compression format from the archive filename extension."""
    name = output.name
    if name.endswith(".tar.zst") or name.endswith(".tar.zstd"):
        return "zst"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "gz"
    if name.endswith(".tar.bz2"):
        return "bz2"
    return "zst"


def tarfile_supports_zstd() -> bool:
    """Return True when this Python's tarfile module can open zstd archives."""
    return "zst" in tarfile.TarFile.OPEN_METH


def zstd_module() -> Any:
    """Return the stdlib or backport zstd module."""
    for module_name in ("compression.zstd", "backports.zstd"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    raise ArchiveError(
        "Zstandard archive support is not available.",
        hints=[
            "Install backports.zstd for Python versions before 3.14,",
            "or choose an archive name ending in .tar.gz or .tar.bz2.",
        ],
    )


@contextmanager
def open_tar_for_write(
    output: Path | BinaryIO,
    compression: str,
    compression_level: int | None,
) -> Iterator[tarfile.TarFile]:
    """Open a tar archive path or binary stream for writing."""
    if compression == "zst" and not tarfile_supports_zstd():
        with zstd_module().open(output, "wb", level=compression_level) as compressed:
            with tarfile.open(fileobj=compressed, mode="w:") as tf:
                yield tf
        return

    mode = f"w:{compression}"
    kwargs = {}
    if compression_level is not None:
        kwargs["compresslevel"] = compression_level
    target = {"name": output} if isinstance(output, Path) else {"fileobj": output}
    with tarfile.open(
        mode=mode,
        **target,
        **kwargs,
    ) as tf:  # ty: ignore[no-matching-overload]
        yield tf


def create_archive(
    root: Path,
    output: Path,
    archive_config: ArchiveConfig,
    *,
    files: list[Path] | None = None,
    root_descriptor: int | None = None,
    bundle_packages: list[Path] | None = None,
    bundle_hashes: dict[str, str] | None = None,
    extra_files: tuple[Path, ...] = (),
    regular_members: tuple[str, ...] = (),
    regular_member_hashes: dict[str, str] | None = None,
    virtual_regular_members: tuple[str, ...] = (),
    capture_output: Callable[[ArchiveOutputCapture], None] | None = None,
    capture_sha256: bool = False,
    expected_output_generation: OutputFileGeneration | None | object = (
        _CURRENT_ARCHIVE_OUTPUT_GENERATION
    ),
    dry_run: bool = False,
) -> Path:
    """Create a tar archive of the workspace at *root*.

    Writes to *output*, creating parent directories as needed.
    If *bundle_packages* is provided, the listed conda package archives
    are added under a ``packages/`` prefix inside the archive.
    """
    output = output.expanduser().absolute()
    if output.is_symlink():
        raise ArchiveError("Archive output cannot be a symbolic link.")
    if expected_output_generation is _CURRENT_ARCHIVE_OUTPUT_GENERATION:
        expected_output_generation = regular_file_generation(output)
    for package in bundle_packages or ():
        if output_paths_collide(output, package):
            raise ArchiveError(
                "Archive output cannot overwrite a bundled package input."
            )

    if files is None:
        files = exclude_archive_output(
            collect_archive_files(root, archive_config, extra_files=extra_files),
            output,
        )
    if root_descriptor is None or dry_run:
        validate_archive_members_for_create(
            root=root,
            files=files,
            bundle_packages=bundle_packages,
            regular_members=frozenset(regular_members),
            virtual_regular_members=frozenset(virtual_regular_members),
        )

    if dry_run:
        return output

    compression = detect_compression(output)

    with atomic_binary_writer(
        output,
        expected_generation=expected_output_generation,
    ) as output_stream:
        hashing_stream = _HashingWriter(output_stream) if capture_sha256 else None
        tar_output = (
            cast("BinaryIO", hashing_stream)
            if hashing_stream is not None
            else output_stream
        )
        with open_tar_for_write(
            tar_output,
            compression,
            archive_config.compression_level,
        ) as tf:
            write_limits = _ArchiveWriteLimits()
            add_files_to_tar(
                tf,
                root,
                files,
                root_descriptor=root_descriptor,
                regular_members=frozenset(regular_members),
                regular_member_hashes=regular_member_hashes,
                write_limits=write_limits,
            )
            if bundle_packages:
                add_packages_to_tar(
                    tf,
                    bundle_packages,
                    expected_hashes=bundle_hashes,
                    write_limits=write_limits,
                )
            validate_tar_members(tf.getmembers())
        if capture_output is not None:
            output_stream.flush()
            captured = os.fstat(output_stream.fileno())
            if hashing_stream is not None and (
                not hashing_stream.sequential
                or hashing_stream.position != captured.st_size
                or output_stream.tell() != captured.st_size
            ):
                raise ArchiveError(
                    "Archive output could not be captured as a sequential stream."
                )
            capture_output(
                ArchiveOutputCapture(
                    sha256=(
                        hashing_stream.digest.hexdigest()
                        if hashing_stream is not None
                        else None
                    ),
                    identity=(captured.st_dev, captured.st_ino),
                    size=captured.st_size,
                )
            )

    return output


def exclude_archive_output(files: list[Path], output: Path) -> list[Path]:
    """Exclude *output* while rejecting physical aliases among workspace inputs."""
    result = []
    for path in files:
        if path.resolve() == output.resolve():
            continue
        if output_paths_collide(path, output):
            raise ArchiveError(
                "Archive output cannot overwrite a hardlinked workspace input."
            )
        result.append(path)
    return result


def validate_archive_members_for_create(
    *,
    root: Path,
    files: list[Path],
    bundle_packages: list[Path] | None = None,
    regular_members: frozenset[str] = frozenset(),
    virtual_regular_members: frozenset[str] = frozenset(),
) -> None:
    """Validate the complete member topology before creating an archive.

    *virtual_regular_members* represents generated files that do not exist
    during a dry run.
    """
    members: list[tarfile.TarInfo] = []
    with tarfile.open(fileobj=BytesIO(), mode="w:") as tf:
        for path in files:
            arcname = path.relative_to(root).as_posix()
            if arcname in virtual_regular_members and not path.exists():
                member = tarfile.TarInfo(arcname)
                member.type = tarfile.REGTYPE
            else:
                dereference = tf.dereference
                tf.dereference = arcname in regular_members
                try:
                    member = tf.gettarinfo(str(path), arcname=arcname)
                finally:
                    tf.dereference = dereference
                if member is None:
                    raise ArchiveError(f"Cannot archive unsupported file: {path}")
            members.append(member)

        for package in bundle_packages or ():
            if package.is_symlink() or not package.is_file():
                raise ArchiveError(
                    f"Cannot bundle package: {package} is not a regular file."
                )
            dereference = tf.dereference
            tf.dereference = True
            try:
                member = tf.gettarinfo(
                    str(package),
                    arcname=f"packages/{package.name}",
                )
            finally:
                tf.dereference = dereference
            if member is None:
                raise ArchiveError(
                    f"Cannot bundle package: {package} is not a regular file."
                )
            members.append(member)

    validate_tar_members(members)


def add_files_to_tar(
    tf: tarfile.TarFile,
    root: Path,
    files: list[Path],
    *,
    root_descriptor: int | None = None,
    regular_members: frozenset[str] = frozenset(),
    regular_member_hashes: dict[str, str] | None = None,
    write_limits: _ArchiveWriteLimits | None = None,
) -> None:
    """Add workspace *files* to the tar, using paths relative to *root*."""
    limits = write_limits if write_limits is not None else _ArchiveWriteLimits()
    publication_root = (
        nullcontext(root_descriptor)
        if root_descriptor is not None
        else anchored_directory(Path(root))
    )
    with publication_root as anchored_root:
        for path in files:
            arcname = path.relative_to(root).as_posix()
            add_archive_file_to_tar(
                tf,
                path,
                arcname,
                root_descriptor=anchored_root,
                require_regular=arcname in regular_members,
                expected_sha256=(regular_member_hashes or {}).get(arcname),
                write_limits=limits,
            )


@contextmanager
def anchored_archive_member_parent(
    path: Path,
    arcname: str,
    root_descriptor: int | None,
) -> Iterator[int | None]:
    """Anchor an archive member parent below the opened workspace root."""
    if root_descriptor is None:
        with anchored_directory(path.parent) as parent_descriptor:
            yield parent_descriptor
        return

    relative = parse_relative_posix_path(arcname, require_canonical=True)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.dup(root_descriptor)
    try:
        for part in relative.parts[:-1]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def add_archive_file_to_tar(
    tf: tarfile.TarFile,
    path: Path,
    arcname: str,
    *,
    root_descriptor: int | None = None,
    require_regular: bool = False,
    expected_sha256: str | None = None,
    write_limits: _ArchiveWriteLimits | None = None,
) -> None:
    """Add one stable regular file or symbolic link without following it."""
    limits = write_limits if write_limits is not None else _ArchiveWriteLimits()
    with anchored_archive_member_parent(path, arcname, root_descriptor) as parent:
        if require_regular:
            add_regular_file_to_tar(
                tf,
                path,
                arcname,
                expected_sha256=expected_sha256,
                parent_descriptor=parent,
                write_limits=limits,
            )
            return
        try:
            current = archive_leaf_stat(path, parent)
        except FileNotFoundError as exc:
            raise ArchiveError(f"Archive input changed before reading: {path}") from exc
        if stat.S_ISREG(current.st_mode):
            add_regular_file_to_tar(
                tf,
                path,
                arcname,
                parent_descriptor=parent,
                write_limits=limits,
            )
            return
        if stat.S_ISLNK(current.st_mode):
            add_symlink_to_tar(
                tf,
                path,
                arcname,
                parent_descriptor=parent,
                write_limits=limits,
            )
            return
        raise ArchiveError(f"Cannot archive unsupported file: {path}")


def archive_leaf_stat(path: Path, parent_descriptor: int | None) -> os.stat_result:
    """Stat one archive member leaf without following it."""
    if parent_descriptor is None:
        return path.lstat()
    return os.stat(
        path.name,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )


def add_regular_file_to_tar(
    tf: tarfile.TarFile,
    path: Path,
    arcname: str,
    *,
    expected_sha256: str | None = None,
    parent_descriptor: int | None = None,
    write_limits: _ArchiveWriteLimits | None = None,
) -> None:
    """Add one no-follow regular-file generation to *tf*."""
    limits = write_limits if write_limits is not None else _ArchiveWriteLimits()
    try:
        reserved = archive_leaf_stat(path, parent_descriptor)
    except FileNotFoundError as exc:
        raise ArchiveError(f"Archive input changed before reading: {path}") from exc
    if not stat.S_ISREG(reserved.st_mode):
        raise ArchiveError(f"Cannot archive regular file safely: {path}")
    reserved_member = tarfile.TarInfo(arcname)
    reserved_member.size = reserved.st_size
    limits.reserve(reserved_member)
    reserved_generation = file_generation(reserved)

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        try:
            descriptor = (
                os.open(path, flags)
                if parent_descriptor is None
                else os.open(path.name, flags, dir_fd=parent_descriptor)
            )
        except OSError as exc:
            raise ArchiveError(f"Cannot archive regular file safely: {path}") from exc
        opened = os.fstat(descriptor)
        try:
            current = archive_leaf_stat(path, parent_descriptor)
        except FileNotFoundError as exc:
            raise ArchiveError(f"Archive input changed before reading: {path}") from exc
        opened_generation = file_generation(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened_generation != reserved_generation
            or file_generation(current) != opened_generation
        ):
            raise ArchiveError(f"Archive input is not a stable regular file: {path}")

        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            dereference = tf.dereference
            tf.dereference = True
            try:
                member = tf.gettarinfo(fileobj=stream, arcname=arcname)
            finally:
                tf.dereference = dereference
            if member is None or not member.isreg():
                raise ArchiveError(f"Archive input changed before reading: {path}")
            validate_tar_member(member)
            if expected_sha256 is None:
                tf.addfile(member, stream)
                actual = None
            else:
                reader = _HashingReader(stream)
                tf.addfile(member, reader)
                actual = reader.digest.hexdigest()
            final = os.fstat(stream.fileno())
        try:
            current = archive_leaf_stat(path, parent_descriptor)
        except FileNotFoundError as exc:
            raise ArchiveError(f"Archive input changed while reading: {path}") from exc
        if (
            file_generation(final) != opened_generation
            or final.st_ctime_ns != opened.st_ctime_ns
            or not stat.S_ISREG(current.st_mode)
            or file_generation(current) != opened_generation
        ):
            raise ArchiveError(f"Archive input changed while reading: {path}")
        if expected_sha256 is not None:
            assert actual is not None
            if actual != expected_sha256:
                raise ArchiveHashMismatchError(
                    path.name,
                    expected=expected_sha256,
                    actual=actual,
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def add_symlink_to_tar(
    tf: tarfile.TarFile,
    path: Path,
    arcname: str,
    *,
    parent_descriptor: int | None = None,
    write_limits: _ArchiveWriteLimits | None = None,
) -> None:
    """Add one stable symbolic-link generation without following its target."""
    try:
        opened = archive_leaf_stat(path, parent_descriptor)
        linkname = (
            os.readlink(path)
            if parent_descriptor is None
            else os.readlink(path.name, dir_fd=parent_descriptor)
        )
    except (FileNotFoundError, OSError) as exc:
        raise ArchiveError(f"Cannot archive symbolic link safely: {path}") from exc
    if not stat.S_ISLNK(opened.st_mode):
        raise ArchiveError(f"Archive input is not a stable symbolic link: {path}")

    member = tarfile.TarInfo(arcname)
    member.mode = opened.st_mode
    member.uid = opened.st_uid
    member.gid = opened.st_gid
    member.size = 0
    member.mtime = opened.st_mtime
    member.type = tarfile.SYMTYPE
    member.linkname = linkname
    try:
        current = archive_leaf_stat(path, parent_descriptor)
    except FileNotFoundError as exc:
        raise ArchiveError(f"Archive input changed before reading: {path}") from exc
    if (
        not member.issym()
        or member.linkname != linkname
        or file_generation(current) != file_generation(opened)
    ):
        raise ArchiveError(f"Archive input changed before reading: {path}")
    limits = write_limits if write_limits is not None else _ArchiveWriteLimits()
    limits.reserve(member)
    tf.addfile(member)


def add_packages_to_tar(
    tf: tarfile.TarFile,
    packages: list[Path],
    *,
    expected_hashes: dict[str, str] | None = None,
    write_limits: _ArchiveWriteLimits | None = None,
) -> None:
    """Add conda package archives under the ``packages/`` archive prefix."""
    limits = write_limits if write_limits is not None else _ArchiveWriteLimits()
    for pkg in packages:
        with anchored_directory(pkg.parent) as parent_descriptor:
            add_regular_file_to_tar(
                tf,
                pkg,
                f"packages/{pkg.name}",
                expected_sha256=(expected_hashes or {}).get(pkg.name),
                parent_descriptor=parent_descriptor,
                write_limits=limits,
            )


def validate_tar_member(
    member: tarfile.TarInfo,
    target: Path | None = None,
) -> PurePosixPath:
    """Return the validated path or raise when *member* escapes *target*.

    Checks for disallowed file types (device nodes, FIFOs, etc.),
    absolute paths, ``..`` components, and symlink targets.
    """
    if member.type not in ALLOWED_TAR_TYPES:
        raise ArchivePathTraversalError(member.name)

    try:
        member_path = parse_relative_archive_path(member.name)
    except ValueError:
        raise ArchivePathTraversalError(member.name) from None

    if target is not None:
        try:
            resolved = target.joinpath(*member_path.parts).resolve()
            resolved.relative_to(target.resolve())
        except ValueError:
            raise ArchivePathTraversalError(member.name)

    if member.issym():
        try:
            link_target = parse_relative_archive_path(
                member.linkname,
                allow_parent=True,
            )
        except ValueError:
            raise ArchivePathTraversalError(member.name) from None

        base = member_path.parent
        normalized_link = posixpath.normpath((base / link_target).as_posix())
        try:
            normalized_path = parse_relative_archive_path(normalized_link)
        except ValueError:
            raise ArchivePathTraversalError(member.name)

        if target is not None:
            resolved_link = target.joinpath(*normalized_path.parts).resolve()
            try:
                resolved_link.relative_to(target.resolve())
            except ValueError:
                raise ArchivePathTraversalError(member.name)
    return member_path


def validate_tar_members(
    members: list[tarfile.TarInfo],
    target: Path | None = None,
) -> None:
    """Validate the paths and extraction topology of all archive *members*."""
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ArchiveError(
            f"Archive contains more than {MAX_ARCHIVE_MEMBERS:,} members."
        )

    expanded_bytes = 0
    path_components = 0
    children: list[dict[str, int]] = [{}]
    node_members: dict[int, tarfile.TarInfo] = {}
    first_descendants: dict[int, tarfile.TarInfo] = {}
    members_by_path: dict[
        tuple[str, ...],
        tuple[PurePosixPath, tarfile.TarInfo],
    ] = {}

    for member in members:
        expanded_bytes = validate_tar_member_limits(member, expanded_bytes)
        member_path = validate_tar_member(member, target)
        path_key = portable_path_key(member_path)
        path_components += len(path_key)
        if path_components > MAX_ARCHIVE_COMPONENTS:
            raise ArchiveError(
                "Archive member paths exceed the maximum cumulative component"
                f" count of {MAX_ARCHIVE_COMPONENTS:,}."
            )

        node = 0
        ancestors: list[int] = []
        for part in path_key:
            parent_member = node_members.get(node)
            if parent_member is not None and not parent_member.isdir():
                raise ArchiveError(
                    f"Archive member '{member.name}' is nested under"
                    f" non-directory member '{parent_member.name}'."
                )
            ancestors.append(node)
            child = children[node].get(part)
            if child is None:
                child = len(children)
                children[node][part] = child
                children.append({})
            node = child

        if node in node_members:
            raise ArchiveError(f"Archive contains duplicate member: {member.name}")

        if not member.isdir():
            descendant = first_descendants.get(node)
            if descendant is not None:
                raise ArchiveError(
                    f"Archive member '{member.name}' conflicts with"
                    f" nested member '{descendant.name}'."
                )

        node_members[node] = member
        members_by_path[path_key] = (member_path, member)
        for ancestor in ancestors:
            first_descendants.setdefault(ancestor, member)
    resolved_links: dict[tuple[str, ...], tarfile.TarInfo | None] = {}
    for member_path, member in members_by_path.values():
        if not member.issym():
            continue
        start_key = portable_path_key(member_path)
        current_key = start_key
        chain: list[tuple[str, ...]] = []
        chain_keys: set[tuple[str, ...]] = set()
        while current_key not in resolved_links:
            if current_key in chain_keys:
                raise ArchiveError(
                    f"Archive link '{member.name}' contains a reference cycle."
                )
            target_entry = members_by_path.get(current_key)
            if target_entry is None:
                terminal = None
                break
            current_path, current = target_entry
            if not current.issym():
                terminal = current
                break
            chain_keys.add(current_key)
            chain.append(current_key)
            link_target = parse_relative_archive_path(
                current.linkname,
                allow_parent=True,
            )
            base = current_path.parent
            target_path = parse_relative_archive_path(
                posixpath.normpath((base / link_target).as_posix())
            )
            current_key = portable_path_key(target_path)
        else:
            terminal = resolved_links[current_key]
        for chain_key in reversed(chain):
            resolved_links[chain_key] = terminal
        if terminal is not None and terminal.isreg():
            expanded_bytes += terminal.size
            if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ArchiveError(
                    "Archive regular files and link fallbacks expand beyond the"
                    f" maximum size of {MAX_ARCHIVE_EXPANDED_BYTES:,} bytes."
                )


def validate_tar_member_limits(
    member: tarfile.TarInfo,
    expanded_bytes: int,
) -> int:
    """Validate one member's resource limits and return its running byte total."""
    if (
        len(member.name.encode("utf-8", errors="surrogateescape"))
        > MAX_ARCHIVE_PATH_BYTES
    ):
        raise ArchiveError(
            "Archive member path exceeds the maximum length of"
            f" {MAX_ARCHIVE_PATH_BYTES:,} bytes."
        )
    if member.issym():
        if (
            len(member.linkname.encode("utf-8", errors="surrogateescape"))
            > MAX_ARCHIVE_PATH_BYTES
        ):
            raise ArchiveError(
                "Archive member link target exceeds the maximum length of"
                f" {MAX_ARCHIVE_PATH_BYTES:,} bytes."
            )
        if len(PurePosixPath(member.linkname).parts) > MAX_ARCHIVE_PATH_DEPTH:
            raise ArchiveError(
                f"Archive member '{member.name}' link target exceeds the maximum"
                f" path depth of {MAX_ARCHIVE_PATH_DEPTH}."
            )
    try:
        member_path = parse_relative_archive_path(member.name)
    except ValueError:
        raise ArchivePathTraversalError(member.name) from None
    if len(member_path.parts) > MAX_ARCHIVE_PATH_DEPTH:
        raise ArchiveError(
            f"Archive member '{member.name}' exceeds the maximum path depth"
            f" of {MAX_ARCHIVE_PATH_DEPTH}."
        )
    if not member.isreg():
        return expanded_bytes
    if member.size < 0:
        raise ArchiveError(f"Archive member '{member.name}' has a negative size.")
    expanded_bytes += member.size
    if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
        raise ArchiveError(
            "Archive regular files expand beyond the maximum size of"
            f" {MAX_ARCHIVE_EXPANDED_BYTES:,} bytes."
        )
    return expanded_bytes


def read_tar_members(tf: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Read archive members while enforcing resource limits before advancing."""
    members: list[tarfile.TarInfo] = []
    expanded_bytes = 0
    while member := tf.next():
        if len(members) >= MAX_ARCHIVE_MEMBERS:
            raise ArchiveError(
                f"Archive contains more than {MAX_ARCHIVE_MEMBERS:,} members."
            )
        expanded_bytes = validate_tar_member_limits(member, expanded_bytes)
        members.append(member)
    return members


@contextmanager
def open_stable_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int | None = None,
) -> Iterator[BinaryIO]:
    """Open one anchored no-follow regular-file generation for streaming."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        with anchored_directory(path.parent) as parent_descriptor:
            try:
                descriptor = (
                    os.open(path, flags)
                    if parent_descriptor is None
                    else os.open(path.name, flags, dir_fd=parent_descriptor)
                )
            except OSError as exc:
                raise ArchiveError(f"{label} cannot be opened safely: {path}") from exc
            opened = os.fstat(descriptor)
            opened_generation = file_generation(opened)
            try:
                current = archive_leaf_stat(path, parent_descriptor)
                live = path.lstat()
            except FileNotFoundError as exc:
                raise ArchiveError(f"{label} changed while opening: {path}") from exc
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or file_generation(current) != opened_generation
                or not stat.S_ISREG(live.st_mode)
                or file_generation(live) != opened_generation
            ):
                raise ArchiveError(f"{label} is not a stable regular file: {path}")
            if maximum_bytes is not None and opened.st_size > maximum_bytes:
                raise ArchiveError(
                    f"{label} exceeds the maximum size of {maximum_bytes:,} bytes."
                )

            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                try:
                    yield stream
                finally:
                    final = os.fstat(stream.fileno())
                    try:
                        current = archive_leaf_stat(path, parent_descriptor)
                        live = path.lstat()
                    except FileNotFoundError as exc:
                        raise ArchiveError(
                            f"{label} changed while reading: {path}"
                        ) from exc
                    if (
                        file_generation(final) != opened_generation
                        or final.st_ctime_ns != opened.st_ctime_ns
                        or not stat.S_ISREG(current.st_mode)
                        or file_generation(current) != opened_generation
                        or not stat.S_ISREG(live.st_mode)
                        or file_generation(live) != opened_generation
                    ):
                        raise ArchiveError(f"{label} changed while reading: {path}")
    except OSError as exc:
        raise ArchiveError(f"{label} cannot be opened safely: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def open_tar(archive_path: Path) -> Iterator[tarfile.TarFile]:
    """Open one stable archive generation without following filesystem links."""
    compression = detect_compression(archive_path)
    with open_stable_regular_file(
        archive_path,
        label="Archive",
        maximum_bytes=MAX_ARCHIVE_RAW_BYTES,
    ) as archive_stream:
        if compression == "zst" and not tarfile_supports_zstd():
            with zstd_module().open(archive_stream, "rb") as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="r:",
                    tarinfo=_BoundedTarInfo,
                ) as tf:
                    yield tf
            return
        with tarfile.open(  # ty: ignore[no-matching-overload]
            fileobj=archive_stream,
            mode=f"r:{compression}",
            tarinfo=_BoundedTarInfo,
        ) as tf:
            yield tf


def ensure_extract_target_empty(target: Path) -> None:
    """Require an archive extraction target that does not yet exist."""
    if target.is_symlink():
        raise ArchiveError("Cannot extract archive into an existing symlink target.")
    if not target.exists():
        return
    raise ArchiveError(
        "Cannot extract archive into an existing target.",
        hints=["Choose a new target path or remove the existing target first."],
    )


@contextmanager
def open_archive_member_parent(
    root_descriptor: int,
    member_path: PurePosixPath,
    directories: dict[tuple[str, ...], tuple[int, int]],
) -> Iterator[int]:
    """Open a member parent below an anchored empty staging directory."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.dup(root_descriptor)
    parts: list[str] = []
    try:
        for part in member_path.parts[:-1]:
            parts.append(part)
            key = tuple(parts)
            expected = directories.get(key)
            if expected is None:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError as exc:
                    raise ArchiveError(
                        "Archive extraction staging changed while creating"
                        f" '{member_path}'."
                    ) from exc
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise ArchiveError(
                    f"Archive extraction parent changed for '{member_path}'."
                ) from exc
            opened = os.fstat(next_descriptor)
            identity = opened.st_dev, opened.st_ino
            if not stat.S_ISDIR(opened.st_mode) or (
                expected is not None and identity != expected
            ):
                os.close(next_descriptor)
                raise ArchiveError(
                    f"Archive extraction parent changed for '{member_path}'."
                )
            directories.setdefault(key, identity)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def extract_tar_member(
    tf: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    root_descriptor: int,
    directories: dict[tuple[str, ...], tuple[int, int]],
) -> None:
    """Extract one validated member beneath an anchored staging descriptor."""
    member_path = parse_relative_archive_path(member.name)
    path_key = tuple(member_path.parts)
    with open_archive_member_parent(
        root_descriptor,
        member_path,
        directories,
    ) as parent_descriptor:
        name = member_path.name
        if member.isdir():
            expected = directories.get(path_key)
            if expected is None:
                try:
                    os.mkdir(name, 0o700, dir_fd=parent_descriptor)
                except FileExistsError as exc:
                    raise ArchiveError(
                        f"Archive extraction staging changed at '{member.name}'."
                    ) from exc
            opened = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            identity = opened.st_dev, opened.st_ino
            if not stat.S_ISDIR(opened.st_mode) or (
                expected is not None and identity != expected
            ):
                raise ArchiveError(
                    f"Archive extraction staging changed at '{member.name}'."
                )
            directories.setdefault(path_key, identity)
            return

        if member.issym():
            linkname = posixpath.normpath(member.linkname)
            try:
                os.symlink(linkname, name, dir_fd=parent_descriptor)
                current = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                current_link = os.readlink(name, dir_fd=parent_descriptor)
            except OSError as exc:
                raise ArchiveError(
                    f"Archive extraction staging changed at '{member.name}'."
                ) from exc
            if not stat.S_ISLNK(current.st_mode) or current_link != linkname:
                raise ArchiveError(
                    f"Archive extraction staging changed at '{member.name}'."
                )
            return

        if not member.isreg():
            raise ArchivePathTraversalError(member.name)
        source = tf.extractfile(member)
        if source is None:
            raise ArchiveError(f"Archive member cannot be read: {member.name}")
        descriptor = -1
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
            opened = os.fstat(descriptor)
            identity = opened.st_dev, opened.st_ino
            remaining = member.size
            mode = (member.mode or 0) & 0o755
            if not mode & 0o100:
                mode &= ~0o111
            mode |= 0o600
            with source, os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ArchiveError(
                            f"Archive member changed while reading: {member.name}"
                        )
                    output.write(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fchmod(output.fileno(), mode)
                final = os.fstat(output.fileno())
            current = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(opened.st_mode)
                or (final.st_dev, final.st_ino) != identity
                or final.st_size != member.size
                or not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != identity
            ):
                raise ArchiveError(
                    f"Archive extraction staging changed at '{member.name}'."
                )
        except FileExistsError as exc:
            raise ArchiveError(
                f"Archive extraction staging changed at '{member.name}'."
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def extract_tar_members(
    tf: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    target: Path,
) -> None:
    """Extract validated members while anchoring every staging write."""
    with anchored_directory(target) as root_descriptor:
        if root_descriptor is None:
            tf.extractall(path=target, members=members, filter="data")
            return
        opened_root = os.fstat(root_descriptor)
        root_identity = opened_root.st_dev, opened_root.st_ino
        directories: dict[tuple[str, ...], tuple[int, int]] = {(): root_identity}
        for member in members:
            extract_tar_member(
                tf,
                member,
                root_descriptor=root_descriptor,
                directories=directories,
            )
        try:
            live_root = target.lstat()
        except FileNotFoundError as exc:
            raise ArchiveError(
                "Archive extraction staging changed while extracting."
            ) from exc
        if (
            not stat.S_ISDIR(live_root.st_mode)
            or (live_root.st_dev, live_root.st_ino) != root_identity
        ):
            raise ArchiveError("Archive extraction staging changed while extracting.")


def extract_archive(archive_path: Path, target: Path) -> Path:
    """Extract *archive_path* into *target* with path traversal protection.

    Every member is validated before extraction. Ownership and unsafe mode
    bits are discarded on every supported Python version.
    """
    ensure_extract_target_empty(target)
    target = target.expanduser().absolute().resolve(strict=False)

    with anchored_directory(target.parent, create=True):
        pass
    with tempfile.TemporaryDirectory(
        dir=target.parent,
        prefix=f".{target.name}.extract-",
    ) as temporary:
        staged = Path(temporary) / "workspace"
        staged.mkdir()
        staged_identity = staged.lstat()
        with open_tar(archive_path) as tf:
            members = read_tar_members(tf)
            validate_tar_members(members, target)
            if not hasattr(tarfile, "data_filter"):
                raise ArchiveError(
                    "Safe archive extraction requires Python's tar data filter.",
                    hints=["Update to a current Python 3.10 patch release or newer."],
                )
            extract_tar_members(tf, members, staged)

        try:
            with (
                anchored_directory(target.parent) as target_descriptor,
                anchored_directory(staged.parent) as source_descriptor,
            ):
                current_staged = archive_leaf_stat(staged, source_descriptor)
                if not stat.S_ISDIR(current_staged.st_mode) or (
                    current_staged.st_dev,
                    current_staged.st_ino,
                ) != (staged_identity.st_dev, staged_identity.st_ino):
                    raise ArchiveError(
                        "Extraction staging directory changed before publication."
                    )
                if source_descriptor is None or target_descriptor is None:
                    rename_noreplace(staged, target)
                    published = target.lstat()
                else:
                    opened_target_parent = os.fstat(target_descriptor)
                    live_target_parent = target.parent.lstat()
                    if not stat.S_ISDIR(live_target_parent.st_mode) or (
                        live_target_parent.st_dev,
                        live_target_parent.st_ino,
                    ) != (
                        opened_target_parent.st_dev,
                        opened_target_parent.st_ino,
                    ):
                        raise ArchiveError(
                            "Extraction target changed while the archive was staged."
                        )
                    try:
                        rename_noreplace(
                            staged.name,
                            target.name,
                            source_dir_fd=source_descriptor,
                            destination_dir_fd=target_descriptor,
                        )
                    except OSError as exc:
                        raise ArchiveError(
                            "Extraction target changed while the archive was staged."
                        ) from exc
                    published = os.stat(
                        target.name,
                        dir_fd=target_descriptor,
                        follow_symlinks=False,
                    )
                live_target = target.lstat()
                if (
                    not stat.S_ISDIR(published.st_mode)
                    or (published.st_dev, published.st_ino)
                    != (staged_identity.st_dev, staged_identity.st_ino)
                    or not stat.S_ISDIR(live_target.st_mode)
                    or (live_target.st_dev, live_target.st_ino)
                    != (staged_identity.st_dev, staged_identity.st_ino)
                ):
                    raise ArchiveError(
                        "Extraction target changed while the archive was staged."
                    )
        except OSError as exc:
            raise ArchiveError(
                "Extraction target changed while the archive was staged."
            ) from exc

    return target


def parse_lockfile_packages(lockfile_path: Path) -> list[dict]:
    """Parse the ``packages`` list from a conda lockfile."""
    data = load_lockfile_data(
        read_regular_file_bytes(
            lockfile_path,
            maximum_bytes=MAX_LOCKFILE_BYTES,
            label="workspace lockfile",
        )
    )
    return data.get("packages", []) or []


def url_to_filename(url: str) -> str:
    """Extract the filename from a conda package URL."""
    filename = Path(urlsplit(url).path).name
    if not filename or not filename.endswith(CONDA_PACKAGE_SUFFIXES):
        raise ArchiveError(
            f"Cannot determine conda package filename from URL: {redact_url_text(url)}",
            hints=[
                "Expected package URLs to end in .conda or .tar.bz2.",
                "Regenerate conda.lock and retry the archive command.",
            ],
        )
    return filename


def collect_bundle_packages(
    lockfile: Path | list[dict],
    cache_dirs: list[Path],
) -> list[Path]:
    """Locate conda packages referenced by the lockfile in local caches.

    Raises :class:`ArchiveError` if any package is missing from all caches.
    """
    packages_data = (
        parse_lockfile_packages(lockfile) if isinstance(lockfile, Path) else lockfile
    )
    result: list[Path] = []
    seen: dict[str, str | None] = {}

    for pkg in packages_data:
        url = pkg.get("conda") or pkg.get("url", "")
        if not url:
            continue
        filename = url_to_filename(url)
        sha256 = pkg.get("sha256")
        fingerprint = str(sha256) if sha256 is not None else None
        if filename in seen:
            previous = seen[filename]
            if previous is None or fingerprint is None or previous != fingerprint:
                raise ArchiveError(
                    f"Package filename collision in lockfile: {filename}",
                    hints=[
                        "The archive bundle stores package archives by filename.",
                        (
                            "Regenerate the lockfile or remove one of the colliding"
                            " packages before bundling."
                        ),
                    ],
                )
            continue
        seen[filename] = fingerprint

        found = False
        for cache_dir in cache_dirs:
            candidate = cache_dir / filename
            if candidate.is_file():
                result.append(candidate)
                found = True
                break

        if not found:
            raise ArchiveError(
                f"Package '{filename}' not found in cache.",
                hints=[
                    "Run 'conda workspace install' to populate the package cache,",
                    "then retry the archive command.",
                ],
            )

    return sorted(result, key=lambda p: p.name)


def build_hash_index(lockfile: Path | list[dict]) -> dict[str, str]:
    """Build a filename-to-SHA256 mapping from lockfile package entries."""
    packages_data = (
        parse_lockfile_packages(lockfile) if isinstance(lockfile, Path) else lockfile
    )
    index: dict[str, str] = {}
    for pkg in packages_data:
        url = pkg.get("conda") or pkg.get("url", "")
        sha256 = pkg.get("sha256")
        if url and sha256 is not None:
            index[url_to_filename(url)] = str(sha256)
    return index


def file_generation(value: os.stat_result) -> FileGeneration:
    """Return metadata shared by stable archive reads and receipt publication."""
    ctime_ns = (
        getattr(value, "st_birthtime_ns", value.st_ctime_ns)
        if os.name == "nt"
        else value.st_ctime_ns
    )
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mode,
        value.st_mtime_ns,
        ctime_ns,
    )


def require_regular_file_generation(
    path: Path,
    expected: FileGeneration,
    *,
    label: str,
) -> None:
    """Require *path* to still name the captured regular-file generation."""
    with anchored_directory(path.parent) as parent_descriptor:
        try:
            current = archive_leaf_stat(path, parent_descriptor)
            live = path.lstat()
        except FileNotFoundError as exc:
            raise ArchiveError(
                f"{label} changed after it was captured: {path}"
            ) from exc
        if (
            not stat.S_ISREG(current.st_mode)
            or file_generation(current) != expected
            or not stat.S_ISREG(live.st_mode)
            or file_generation(live) != expected
        ):
            raise ArchiveError(f"{label} changed after it was captured: {path}")


def file_sha256_with_generation(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
    maximum_bytes: int | None = None,
    label: str = "File",
) -> tuple[str, FileGeneration]:
    """Hash one no-follow regular-file generation and return its metadata."""
    with open_stable_regular_file(
        path,
        label=label,
        maximum_bytes=maximum_bytes,
    ) as stream:
        opened = os.fstat(stream.fileno())
        if (
            expected_identity is not None
            and (
                opened.st_dev,
                opened.st_ino,
            )
            != expected_identity
        ):
            raise ArchiveError(f"{label} changed while opening: {path}")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        generation = file_generation(opened)
    return digest.hexdigest(), generation


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of one stable no-follow file generation."""
    digest, _ = file_sha256_with_generation(path)
    return digest


def verify_package_hashes(
    packages: list[Path],
    lockfile: Path | list[dict],
) -> None:
    """Verify SHA256 hashes of *packages* against the lockfile.

    Raises :class:`ArchiveHashMismatchError` on the first mismatch.
    """
    expected = build_hash_index(lockfile)

    for pkg_path in packages:
        exp_hash = expected.get(pkg_path.name)
        if not exp_hash:
            raise ArchiveError(
                f"Cannot verify bundled package '{pkg_path.name}'.",
                hints=[
                    "No SHA256 entry for this package was found in conda.lock.",
                    (
                        "Regenerate conda.lock with a current conda-workspaces version"
                        " before bundling or priming package caches."
                    ),
                ],
            )
        try:
            actual_hash = file_sha256(pkg_path)
        except ArchiveError as exc:
            raise ArchiveError(
                f"Bundled package source cannot be read safely: {pkg_path.name}"
            ) from exc
        if actual_hash != exp_hash:
            raise ArchiveHashMismatchError(
                pkg_path.name, expected=exp_hash, actual=actual_hash
            )


def inspect_archive(archive_path: Path) -> dict[str, object]:
    """Validate archive members and return metadata without extracting."""
    with open_tar(archive_path) as tf:
        members = read_tar_members(tf)
        validate_tar_members(members)
    manifest_members = [
        member
        for member in members
        if member.name in MANIFEST_FILENAMES and member.isreg()
    ]
    if any(member.name in MANIFEST_FILENAMES and member.islnk() for member in members):
        raise ArchiveError("Workspace manifest is not a regular file.")

    lock_members = [member for member in members if member.name == "conda.lock"]
    if lock_members and not lock_members[0].isreg():
        raise ArchiveError("The workspace lockfile must be a regular file.")

    package_members = [
        member
        for member in members
        if member.name.startswith("packages/")
        and member.name.endswith(CONDA_PACKAGE_SUFFIXES)
    ]
    for member in package_members:
        if (
            PurePosixPath(member.name).parent != PurePosixPath("packages")
            or not member.isreg()
        ):
            raise ArchiveError(
                "Bundled packages must be regular files and direct children"
                " of packages/."
            )

    return {
        "has_manifest": bool(manifest_members),
        "has_lockfile": bool(lock_members),
        "has_packages": len(package_members) > 0,
        "package_count": len(package_members),
    }
