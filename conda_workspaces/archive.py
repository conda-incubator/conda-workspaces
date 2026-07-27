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
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from os.path import expanduser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from conda_lockfiles.load_yaml import load_yaml

from .exceptions import (
    ArchiveError,
    ArchiveHashMismatchError,
    ArchivePathTraversalError,
    LockfileNotFoundError,
    LockfileStaleError,
    WorkspaceParseError,
)
from .lockfile import load_lockfile_data
from .manifests import find_parser
from .models import LockfileStatus
from .paths import (
    has_absolute_path_syntax,
    is_path_segment,
    output_paths_collide,
    parse_relative_posix_path,
    validate_directory_output,
    validate_file_output,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any, BinaryIO

    from .context import WorkspaceContext
    from .models import ArchiveConfig
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

ALLOWED_TAR_TYPES: frozenset[bytes] = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
    }
)
"""Tar member types accepted during extraction."""

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
class WorkspaceArchive:
    """High-level API for creating, extracting, and installing archives."""

    path: Path
    receipt: bool | str | Path | None = None

    def __init__(self, path: str | Path, receipt: bool | str | Path | None = None):
        object.__setattr__(self, "path", Path(path).expanduser().resolve())
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
        from .manifests import clear_workspace_manifest_caches, detect_and_parse
        from .models import ArchiveConfig

        clear_workspace_manifest_caches()
        _, config = detect_and_parse(workspace)
        ctx = WorkspaceContext(config)
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

        validate_file_output(output_path)
        if lock:
            validate_file_output(lock_path)
        if receipt_path is not None:
            validate_file_output(receipt_path)
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
            if lock_data is None:
                lock_data = load_lockfile_data(lock_path.read_bytes())
            from .receipts import ReceiptInventory

            ReceiptInventory.from_lockfile_data(
                lock_data,
                environment_prefixes=receipt_environment_prefixes(
                    config_environments=list(config.environments),
                    ctx_root=ctx.root,
                    env_prefix=ctx.env_prefix,
                ),
            )

        bundle_packages = None
        if bundle:
            from conda.base.context import context as conda_context

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
                            f"The {output_label} cannot overwrite a bundled package."
                        )
            verify_package_hashes(bundle_packages, lock_source)

        previous_lock = (
            lock_path.read_bytes()
            if lock_content is not None and lock_path.exists()
            else None
        )
        try:
            if lock_content is not None and not dry_run:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(lock_content, encoding="utf-8")
            archive_path = create_archive(
                ctx.root,
                output_path,
                archive_config,
                bundle_packages=bundle_packages,
                extra_files=extra_files,
                regular_members=(
                    manifest_path.relative_to(ctx.root).as_posix(),
                    lock_path.relative_to(ctx.root).as_posix(),
                ),
                virtual_regular_members=(
                    (lock_path.relative_to(ctx.root).as_posix(),)
                    if lock and not lock_path.exists()
                    else ()
                ),
                dry_run=dry_run,
            )
        except BaseException:
            if lock_content is not None and not dry_run:
                if previous_lock is None:
                    lock_path.unlink(missing_ok=True)
                else:
                    lock_path.write_bytes(previous_lock)
            raise

        if receipt_path is not None and not dry_run:
            receipt_obj = cls.build_receipt(
                ctx=ctx,
                archive_path=archive_path,
                archive_config=archive_config,
                manifest_path=manifest_path,
                lockfile_path=lock_path,
                options={
                    "bundle": bundle,
                    "lock": lock,
                    "include": list(archive_config.include),
                    "exclude": list(archive_config.exclude),
                    "compressionLevel": archive_config.compression_level,
                },
            )
            receipt_obj.write(receipt_path)

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
                    "Receipt verification requires the workspace manifest and"
                    " conda.lock to be included in the archive.",
                    "Remove matching include/exclude filters or run without --receipt.",
                ],
            )

    @staticmethod
    def build_receipt(
        *,
        ctx: WorkspaceContext,
        archive_path: Path,
        archive_config: ArchiveConfig,
        manifest_path: Path,
        lockfile_path: Path,
        options: dict[str, object],
    ) -> ArchiveReceipt:
        """Build the external receipt for a newly created archive."""
        from .receipts import ArchiveReceipt

        return ArchiveReceipt.build(
            root=ctx.root,
            archive_path=archive_path,
            archive_config=archive_config,
            manifest_path=manifest_path,
            lockfile_path=lockfile_path,
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
        target_existed = target_path.exists()
        target_identity = None
        target_timestamps = None
        if target_existed:
            target_stat = target_path.stat()
            target_identity = (target_stat.st_dev, target_stat.st_ino)
            target_timestamps = (target_stat.st_atime_ns, target_stat.st_mtime_ns)
        ensure_extract_target_empty(requested_target)
        validate_directory_output(requested_target)

        if require_sha256 and self.receipt_path is None:
            raise ArchiveError("--require-sha256 requires --receipt.")
        archive_path = self.require_existing_archive()
        info = inspect_archive(archive_path)
        if not info["has_manifest"]:
            raise ArchiveError(
                "Not a workspace archive: no manifest found.",
                hints=["This does not appear to be a conda workspace archive."],
            )
        receipt = self.verify() if self.receipt_path is not None else None
        if receipt is not None:
            with open_tar(archive_path) as tar:
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

        temporary_parent = None
        if not dry_run:
            temporary_parent = target_path.parent
            while not temporary_parent.exists():
                temporary_parent = temporary_parent.parent
        with tempfile.TemporaryDirectory(
            prefix="conda-workspaces-",
            dir=temporary_parent,
        ) as temporary:
            staged = extract_archive(archive_path, Path(temporary) / "workspace")
            if receipt is not None:
                receipt.verify_extracted(staged, require_sha256=require_sha256)
            cache_priming_skipped = bool(
                info["has_packages"] and prime_cache and receipt is None
            )
            cache_plan: list[tuple[str, Path]] = []
            if info["has_packages"] and prime_cache and receipt is not None:
                if package_cache is None:
                    from conda.base.context import context as conda_context

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
                for package in packages:
                    destination = cache_path / package.name
                    if destination.is_symlink():
                        raise ArchiveError(
                            "Package cache destinations cannot be symbolic links."
                        )
                    validate_file_output(destination)
                    if destination.exists():
                        expected = file_sha256(package)
                        actual = file_sha256(destination)
                        if actual != expected:
                            raise ArchiveHashMismatchError(
                                package.name,
                                expected=expected,
                                actual=actual,
                            )
                        continue
                    cache_plan.append((package.name, destination))

            if validate_workspace is not None:
                validate_workspace(staged, receipt)

            primed_packages = len(cache_plan)
            if dry_run:
                extracted = target_path
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if requested_target.resolve() != target_path:
                    raise ArchiveError(
                        "Extraction target changed while the archive was staged."
                    )
                if target_existed:
                    try:
                        target_stat = target_path.lstat()
                    except FileNotFoundError:
                        target_stat = None
                    if (
                        target_stat is None
                        or (target_stat.st_dev, target_stat.st_ino) != target_identity
                    ):
                        raise ArchiveError(
                            "Extraction target changed while the archive was staged."
                        )
                    assert target_timestamps is not None
                    ensure_extract_target_empty(target_path)
                    moved: list[Path] = []
                    try:
                        for child in staged.iterdir():
                            destination = target_path / child.name
                            child.rename(destination)
                            moved.append(destination)
                    except BaseException:
                        for destination in reversed(moved):
                            destination.rename(staged / destination.name)
                        raise
                    finally:
                        if os.utime in os.supports_follow_symlinks:
                            os.utime(
                                target_path,
                                ns=target_timestamps,
                                follow_symlinks=False,
                            )
                        else:
                            os.utime(target_path, ns=target_timestamps)
                    staged.rmdir()
                else:
                    if target_path.exists() or target_path.is_symlink():
                        raise ArchiveError(
                            "Extraction target changed while the archive was staged."
                        )
                    staged.rename(target_path)
                extracted = target_path
                for name, destination in cache_plan:
                    if not destination.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(extracted / "packages" / name, destination)

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
                    "Pass --prefix to declare the final runtime prefix for"
                    " the selected environment.",
                ],
            )
        if final_prefix is not None:
            final_prefix = expanduser(final_prefix)
            if not is_absolute_runtime_prefix(final_prefix):
                raise ArchiveError(
                    "--prefix must be an absolute path.",
                    hints=["Pass an absolute runtime prefix such as /opt/runtime."],
                )

        install_prefix = Path(final_prefix) if final_prefix is not None else None
        runtime_prefix = None
        if final_prefix is not None:
            if dest is not None:
                dest_path = Path(dest).expanduser().resolve()
                install_prefix = dest_path / runtime_prefix_relative_path(final_prefix)
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
            from .manifests import clear_workspace_manifest_caches, detect_and_parse

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

            clear_workspace_manifest_caches()
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
        from .manifests import clear_workspace_manifest_caches, detect_and_parse

        clear_workspace_manifest_caches()
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


def is_absolute_runtime_prefix(prefix: str) -> bool:
    """Return whether *prefix* is absolute as a POSIX or Windows path."""
    return has_absolute_path_syntax(prefix)


def runtime_prefix_relative_path(prefix: str) -> Path:
    """Return *prefix* relative to its root using host path separators."""
    posix_prefix = PurePosixPath(prefix)
    if posix_prefix.is_absolute():
        return Path(*posix_prefix.relative_to(posix_prefix.anchor).parts)

    windows_prefix = PureWindowsPath(prefix)
    return Path(*windows_prefix.relative_to(windows_prefix.anchor).parts)


def file_contains_bytes(
    path: Path, needle: bytes, *, chunk_size: int = 1024 * 1024
) -> bool:
    """Return whether *path* contains *needle* without loading it all at once."""
    if not needle:
        return False

    overlap = b""
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(chunk_size):
                data = overlap + chunk
                if needle in data:
                    return True
                overlap = data[-(len(needle) - 1) :] if len(needle) > 1 else b""
    except OSError:
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
    for excl in BUILTIN_EXCLUDE_DIRS:
        if rel_path == excl or rel_path.startswith(excl + "/"):
            return True
    if matches_patterns(rel_path, BUILTIN_SENSITIVE_EXCLUDE_EXCEPTIONS):
        return False
    return matches_patterns(rel_path, BUILTIN_SENSITIVE_EXCLUDE_PATTERNS)


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
    bundle_packages: list[Path] | None = None,
    extra_files: tuple[Path, ...] = (),
    regular_members: tuple[str, ...] = (),
    virtual_regular_members: tuple[str, ...] = (),
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
    for package in bundle_packages or ():
        if output_paths_collide(output, package):
            raise ArchiveError(
                "Archive output cannot overwrite a bundled package input."
            )

    files = collect_archive_files(root, archive_config, extra_files=extra_files)
    files = exclude_archive_output(files, output)
    validate_archive_members_for_create(
        root=root,
        files=files,
        bundle_packages=bundle_packages,
        regular_members=frozenset(regular_members),
        virtual_regular_members=frozenset(virtual_regular_members),
    )

    if dry_run:
        return output

    output.parent.mkdir(parents=True, exist_ok=True)
    compression = detect_compression(output)

    with open_tar_for_write(
        output, compression, archive_config.compression_level
    ) as tf:
        add_files_to_tar(
            tf,
            root,
            files,
            regular_members=frozenset(regular_members),
        )
        if bundle_packages:
            add_packages_to_tar(tf, bundle_packages)

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
    regular_members: frozenset[str] = frozenset(),
) -> None:
    """Add workspace *files* to the tar, using paths relative to *root*."""
    for path in files:
        arcname = path.relative_to(root).as_posix()
        if arcname in regular_members:
            dereference = tf.dereference
            tf.dereference = True
            try:
                tf.add(str(path), arcname=arcname)
            finally:
                tf.dereference = dereference
        else:
            tf.add(str(path), arcname=arcname)


def add_packages_to_tar(tf: tarfile.TarFile, packages: list[Path]) -> None:
    """Add conda package archives under the ``packages/`` archive prefix."""
    for pkg in packages:
        if pkg.is_symlink() or not pkg.is_file():
            raise ArchiveError(f"Cannot bundle package: {pkg} is not a regular file.")
        arcname = f"packages/{pkg.name}"
        dereference = tf.dereference
        tf.dereference = True
        try:
            tf.add(str(pkg), arcname=arcname)
        finally:
            tf.dereference = dereference


def validate_tar_member(
    member: tarfile.TarInfo,
    target: Path | None = None,
) -> None:
    """Raise :class:`ArchivePathTraversalError` if *member* escapes *target*.

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

    if member.issym() or member.islnk():
        try:
            link_target = parse_relative_archive_path(
                member.linkname,
                allow_parent=True,
            )
        except ValueError:
            raise ArchivePathTraversalError(member.name) from None

        base = member_path.parent if member.issym() else PurePosixPath()
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


def validate_tar_members(
    members: list[tarfile.TarInfo],
    target: Path | None = None,
) -> None:
    """Validate the paths and extraction topology of all archive *members*."""
    seen: dict[tuple[str, ...], tuple[PurePosixPath, tarfile.TarInfo]] = {}
    materialized_files: set[PurePosixPath] = set()

    for member in members:
        validate_tar_member(member, target)
        member_path = parse_relative_archive_path(member.name)
        path_parts = member_path.parts
        if path_parts in seen:
            raise ArchiveError(f"Archive contains duplicate member: {member.name}")

        for size in range(len(path_parts) - 1, 0, -1):
            parent = seen.get(path_parts[:size])
            if parent is not None and not parent[1].isdir():
                raise ArchiveError(
                    f"Archive member '{member.name}' is nested under"
                    f" non-directory member '{parent[0].as_posix()}'."
                )

        if not member.isdir():
            descendant = next(
                (
                    path
                    for key, (path, _) in seen.items()
                    if key[: len(path_parts)] == path_parts
                    and len(key) > len(path_parts)
                ),
                None,
            )
            if descendant is not None:
                raise ArchiveError(
                    f"Archive member '{member.name}' conflicts with"
                    f" nested member '{descendant.as_posix()}'."
                )

        if member.islnk():
            link_path = parse_relative_archive_path(posixpath.normpath(member.linkname))
            if link_path not in materialized_files:
                raise ArchiveError(
                    f"Archive hardlink '{member.name}' refers to an"
                    " unavailable earlier file."
                )

        seen[path_parts] = (member_path, member)
        if member.isreg() or member.islnk():
            materialized_files.add(member_path)


@contextmanager
def open_tar(archive_path: Path) -> Iterator[tarfile.TarFile]:
    """Open a tar archive, handling zstandard decompression transparently."""
    compression = detect_compression(archive_path)
    if compression == "zst" and not tarfile_supports_zstd():
        with zstd_module().open(archive_path, "rb") as compressed:
            with tarfile.open(fileobj=compressed, mode="r:") as tf:
                yield tf
        return
    with tarfile.open(  # ty: ignore[no-matching-overload]
        archive_path, f"r:{compression}"
    ) as tf:
        yield tf


def ensure_extract_target_empty(target: Path) -> None:
    """Reject archive extraction into non-empty or unsafe targets."""
    if target.is_symlink():
        raise ArchiveError("Cannot extract archive into an existing symlink target.")
    if not target.exists():
        return
    if not target.is_dir():
        raise ArchiveError(
            "Cannot extract archive into an existing non-directory target."
        )
    try:
        target_has_files = any(target.iterdir())
    except OSError as exc:
        raise ArchiveError(
            f"Cannot inspect target before archive extraction: {target}"
        ) from exc
    if target_has_files:
        raise ArchiveError(
            "Cannot extract archive into a non-empty target.",
            hints=["Choose an empty target directory or remove existing files first."],
        )


def extract_archive(archive_path: Path, target: Path) -> Path:
    """Extract *archive_path* into *target* with path traversal protection.

    Every member is validated before extraction. On Python 3.12+ the
    ``filter="data"`` parameter provides additional defense-in-depth.
    """
    ensure_extract_target_empty(target)
    target = target.resolve()

    with open_tar(archive_path) as tf:
        members = tf.getmembers()
        validate_tar_members(members, target)
        target.mkdir(parents=True, exist_ok=True)
        if hasattr(tarfile, "data_filter"):
            tf.extractall(path=target, members=members, filter="data")
        else:
            tf.extractall(path=target, members=members)

    return target


def parse_lockfile_packages(lockfile_path: Path) -> list[dict]:
    """Parse the ``packages`` list from a conda lockfile."""
    data = load_lockfile_data(lockfile_path.read_bytes())
    return data.get("packages", []) or []


def url_to_filename(url: str) -> str:
    """Extract the filename from a conda package URL."""
    filename = Path(urlsplit(url).path).name
    if not filename or not filename.endswith(CONDA_PACKAGE_SUFFIXES):
        raise ArchiveError(
            f"Cannot determine conda package filename from URL: {url}",
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
                        "Regenerate the lockfile or remove one of the colliding"
                        " packages before bundling.",
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


def file_sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of *path* without reading it all at once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
                    "Regenerate conda.lock with a current conda-workspaces version"
                    " before bundling or priming package caches.",
                ],
            )
        actual_hash = file_sha256(pkg_path)
        if actual_hash != exp_hash:
            raise ArchiveHashMismatchError(
                pkg_path.name, expected=exp_hash, actual=actual_hash
            )


def inspect_archive(archive_path: Path) -> dict[str, object]:
    """Validate archive members and return metadata without extracting."""
    with open_tar(archive_path) as tf:
        members = tf.getmembers()
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
