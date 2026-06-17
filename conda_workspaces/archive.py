"""Archive creation and extraction for conda workspaces.

Provides functions for collecting workspace files, creating tar archives
(gzip or zstandard), extracting with path traversal protection, bundling
conda packages for offline use, and inspecting archive contents.
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib
import shutil
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from os.path import expanduser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from conda_lockfiles.load_yaml import load_yaml

from .exceptions import (
    ArchiveError,
    ArchiveHashMismatchError,
    ArchivePathTraversalError,
)
from .paths import has_absolute_path_syntax, is_path_segment, parse_relative_posix_path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

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

MANIFEST_SEARCH_FILENAMES = ("conda.toml", "pixi.toml", "pyproject.toml")
"""Workspace manifest filenames in parser search order."""

MANIFEST_FILENAMES = frozenset(MANIFEST_SEARCH_FILENAMES)
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
    attestation_path: Path | None
    verified: bool
    attestation_verified: bool
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
    attestation_path: Path | None
    verified: bool
    attestation_verified: bool
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
    workspace_attestation_path: Path | None = None

    def __init__(
        self,
        path: str | Path,
        receipt: bool | str | Path | None = None,
        workspace_attestation_path: str | Path | None = None,
    ):
        object.__setattr__(self, "path", Path(path).expanduser().resolve())
        object.__setattr__(self, "receipt", receipt)
        object.__setattr__(
            self,
            "workspace_attestation_path",
            (
                Path(workspace_attestation_path).expanduser().resolve()
                if workspace_attestation_path is not None
                else None
            ),
        )

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
        sign: bool = False,
        identity_token: str | None = None,
    ) -> WorkspaceArchive:
        """Create an archive for *workspace* and return its handle."""
        from .context import WorkspaceContext
        from .lockfile import generate_lockfile, lockfile_path
        from .manifests import detect_and_parse
        from .models import ArchiveConfig

        _, config = detect_and_parse(workspace)
        ctx = WorkspaceContext(config)

        if lock:
            from .resolver import resolve_all_environments

            resolved_envs = resolve_all_environments(config, ctx.platform)
            generate_lockfile(ctx, resolved_envs, config=config)

        archive_config = ArchiveConfig(
            include=config.archive.include,
            exclude=config.archive.exclude + tuple(exclude),
            compression=config.archive.compression,
            compression_level=config.archive.compression_level,
        )
        output_path = cls.default_output_path(ctx, output)
        archive = cls(output_path, receipt=receipt)
        receipt_path = archive.receipt_path
        manifest_path = Path(config.manifest_path)
        lock_path = lockfile_path(ctx)
        attestation_path = None

        if receipt_path is not None:
            archive.validate_receipt_inputs(
                root=ctx.root,
                output=output_path,
                archive_config=archive_config,
                manifest_path=manifest_path,
                lockfile_path=lock_path,
                receipt_path=receipt_path,
            )
        if sign:
            archive.validate_attestation_inputs(
                root=ctx.root,
                output=output_path,
                archive_config=archive_config,
                manifest_path=manifest_path,
                lockfile_path=lock_path,
            )

        bundle_packages = None
        if bundle:
            from conda.base.context import context as conda_context

            if not lock_path.is_file():
                raise ArchiveError(
                    "Cannot bundle packages: no conda.lock found.",
                    hints=["Run 'conda workspace lock' first."],
                )
            cache_dirs = [Path(d) for d in conda_context.pkgs_dirs]
            bundle_packages = collect_bundle_packages(lock_path, cache_dirs)
            verify_package_hashes(bundle_packages, lock_path)

        extra_files: tuple[Path, ...] = ()
        if sign:
            from .attestations import write_workspace_attestation

            attestation_path = write_workspace_attestation(
                root=ctx.root,
                manifest_path=manifest_path,
                lockfile_path=lock_path,
                identity_token=identity_token,
            )
            extra_files = (attestation_path,)

        archive_path = create_archive(
            ctx.root,
            output_path,
            archive_config,
            bundle_packages=bundle_packages,
            extra_files=extra_files,
        )

        if receipt_path is not None:
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

        return cls(
            archive_path,
            receipt=receipt_path,
            workspace_attestation_path=attestation_path,
        )

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

    @classmethod
    def validate_receipt_inputs(
        cls,
        *,
        root: Path,
        output: Path,
        archive_config: ArchiveConfig,
        manifest_path: Path,
        lockfile_path: Path,
        receipt_path: Path,
    ) -> None:
        """Validate inputs required to write a receipt for a new archive."""
        if receipt_path.resolve() == output.resolve():
            raise ArchiveError(
                "Receipt path cannot be the archive path.",
                hints=["Choose a separate JSON path for --receipt."],
            )
        if not manifest_path.is_file():
            raise ArchiveError(
                "Cannot write receipt: workspace manifest was not found."
            )
        if not lockfile_path.is_file():
            raise ArchiveError(
                "Cannot write receipt: no conda.lock found.",
                hints=["Run 'conda workspace lock' first."],
            )
        cls.validate_required_archive_members(
            root=root,
            output=output,
            archive_config=archive_config,
            required_members={
                "workspace manifest": manifest_path,
                "workspace lockfile": lockfile_path,
            },
            action="write receipt",
            hints=[
                "Receipt verification requires the workspace manifest and"
                " conda.lock to be included in the archive.",
                "Remove matching include/exclude filters or run without --receipt.",
            ],
        )

    @classmethod
    def validate_attestation_inputs(
        cls,
        *,
        root: Path,
        output: Path,
        archive_config: ArchiveConfig,
        manifest_path: Path,
        lockfile_path: Path,
    ) -> None:
        """Validate inputs required to sign a workspace archive."""
        if not manifest_path.is_file():
            raise ArchiveError("Cannot sign archive: workspace manifest was not found.")
        if not lockfile_path.is_file():
            raise ArchiveError(
                "Cannot sign archive: no conda.lock found.",
                hints=["Run 'conda workspace lock' first."],
            )
        cls.validate_required_archive_members(
            root=root,
            output=output,
            archive_config=archive_config,
            required_members={
                "workspace manifest": manifest_path,
                "workspace lockfile": lockfile_path,
            },
            action="sign archive",
            hints=[
                "Attestation verification requires the workspace manifest and"
                " conda.lock to be included in the archive.",
                "Remove matching include/exclude filters or run without --sign.",
            ],
        )

    @staticmethod
    def validate_required_archive_members(
        *,
        root: Path,
        output: Path,
        archive_config: ArchiveConfig,
        required_members: dict[str, Path],
        action: str,
        hints: list[str],
    ) -> None:
        """Validate that archive verification inputs are included as members."""
        archive_members = {
            path.relative_to(root).as_posix()
            for path in collect_archive_files(root, archive_config)
            if path.resolve() != output.resolve()
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
                f"Cannot {action}: archive would not include {missing[0]}.",
                hints=hints,
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
        verify_attestation: bool = False,
        trusted_identities: tuple[Any, ...] = (),
    ) -> WorkspaceArchiveExtractResult:
        """Extract the archive and optionally prime bundled package cache files."""
        if require_sha256 and self.receipt_path is None:
            raise ArchiveError("--require-sha256 requires --receipt.")

        archive_path = self.require_existing_archive()
        info = inspect_archive(archive_path)
        if not info["has_manifest"]:
            raise ArchiveError(
                "Not a workspace archive: no manifest found.",
                hints=["This does not appear to be a conda workspace archive."],
            )

        target_path = (
            Path(target).expanduser() if target is not None else self.default_target()
        )
        receipt = self.verify() if self.receipt_path is not None else None
        attestation_path = None
        attestation_verified = False
        if receipt is None and not verify_attestation:
            extracted = extract_archive(archive_path, target_path)
        else:
            (
                extracted,
                attestation_path,
                attestation_verified,
            ) = _extract_verified_archive(
                archive_path,
                target_path,
                receipt,
                require_sha256=require_sha256,
                verify_attestation=verify_attestation,
                trusted_identities=trusted_identities,
            )

        primed_packages = 0
        cache_priming_skipped = False
        if info["has_packages"] and prime_cache:
            if receipt is None and not attestation_verified:
                cache_priming_skipped = True
            else:
                if package_cache is None:
                    from conda.base.context import context as conda_context

                    cache_path = Path(conda_context.pkgs_dirs[0])
                else:
                    cache_path = Path(package_cache)
                primed_packages = prime_package_cache(
                    extracted,
                    cache_path,
                    verified=True,
                )

        return WorkspaceArchiveExtractResult(
            target=extracted,
            receipt_path=self.receipt_path,
            attestation_path=attestation_path,
            verified=receipt is not None,
            attestation_verified=attestation_verified,
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
        verify_attestation: bool = False,
        trusted_identities: tuple[Any, ...] = (),
        install_handler: Callable[[Path, str | None, Path | None, str | None], int]
        | None = None,
    ) -> WorkspaceArchiveInstallResult:
        """Extract the archive and install environments from its lockfile."""
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

        extract_result = self.extract(
            target=target,
            require_sha256=require_sha256,
            prime_cache=prime_cache,
            package_cache=package_cache,
            verify_attestation=verify_attestation,
            trusted_identities=trusted_identities,
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
            attestation_path=extract_result.attestation_path,
            verified=extract_result.verified,
            attestation_verified=extract_result.attestation_verified,
            info=extract_result.info,
            return_code=return_code,
            primed_packages=extract_result.primed_packages,
            cache_priming_skipped=extract_result.cache_priming_skipped,
            prefix_reference_matches=prefix_matches,
            prefix_reference_matches_truncated=prefix_matches_truncated,
        )

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

        _, config = detect_and_parse(workspace)
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


def extract_verified_archive(
    archive_path: Path,
    target: Path,
    receipt: ArchiveReceipt,
    *,
    require_sha256: bool = False,
) -> Path:
    """Extract to a staging directory, verify the receipt, then move into *target*."""
    extracted, _, _ = _extract_verified_archive(
        archive_path,
        target,
        receipt,
        require_sha256=require_sha256,
    )
    return extracted


def _extract_verified_archive(
    archive_path: Path,
    target: Path,
    receipt: ArchiveReceipt | None,
    *,
    require_sha256: bool = False,
    verify_attestation: bool = False,
    trusted_identities: tuple[Any, ...] = (),
) -> tuple[Path, Path | None, bool]:
    """Extract to a staging directory, verify, then move into *target*."""
    ensure_extract_target_empty(target)
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{target.name}.verify-", dir=target.parent))
    attestation_path = None
    attestation_verified = False
    try:
        extract_archive(archive_path, staged)
        if receipt is not None:
            receipt.verify_extracted(staged, require_sha256=require_sha256)
        if verify_attestation:
            from .attestations import (
                default_attestation_path,
                verify_workspace_attestation,
            )

            attestation_path = default_attestation_path(staged / "conda.lock")
            manifest_path = next(
                (
                    staged / filename
                    for filename in MANIFEST_SEARCH_FILENAMES
                    if (staged / filename).is_file()
                ),
                None,
            )
            if manifest_path is None:
                raise ArchiveError(
                    "Cannot verify archive attestation: workspace manifest was "
                    "not found."
                )
            verify_workspace_attestation(
                root=staged,
                identities=trusted_identities,
                manifest_path=manifest_path,
                lockfile_path=staged / "conda.lock",
                bundle_path=attestation_path,
            )
            attestation_verified = True
        if target.exists():
            target.rmdir()
        staged.rename(target)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    if attestation_path is not None:
        attestation_path = target / attestation_path.relative_to(staged)
    return target, attestation_path, attestation_verified


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
) -> list[Path]:
    """Collect workspace files eligible for archiving.

    In git repos, only tracked files are included. Otherwise all files
    under *root* are considered, filtered by builtin and user excludes.
    """
    if is_git_repo(root):
        candidates = git_tracked_files(root)
    else:
        candidates = [p for p in root.rglob("*") if p.is_file()]

    result: list[Path] = []
    for path in candidates:
        rel = path.relative_to(root).as_posix()
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
    output: Path, compression: str, compression_level: int | None
) -> Iterator[tarfile.TarFile]:
    """Open a tar archive for writing, optionally setting compression level."""
    if compression == "zst" and not tarfile_supports_zstd():
        with zstd_module().open(output, "wb", level=compression_level) as compressed:
            with tarfile.open(fileobj=compressed, mode="w:") as tf:
                yield tf
        return

    mode = f"w:{compression}"
    kwargs = {}
    if compression_level is not None:
        kwargs["compresslevel"] = compression_level
    with tarfile.open(output, mode, **kwargs) as tf:  # ty: ignore[no-matching-overload]
        yield tf


def create_archive(
    root: Path,
    output: Path,
    archive_config: ArchiveConfig,
    *,
    bundle_packages: list[Path] | None = None,
    extra_files: tuple[Path, ...] = (),
) -> Path:
    """Create a tar archive of the workspace at *root*.

    Writes to *output*, creating parent directories as needed.
    If *bundle_packages* is provided, the listed conda package archives
    are added under a ``packages/`` prefix inside the archive.
    """
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    files = collect_archive_files(root, archive_config)
    seen_files = {path.resolve() for path in files}
    for extra in extra_files:
        extra = extra.resolve()
        try:
            extra.relative_to(root.resolve())
        except ValueError:
            raise ArchiveError(
                f"Cannot include archive sidecar outside workspace: {extra}"
            ) from None
        if extra.is_file() and extra not in seen_files:
            files.append(extra)
            seen_files.add(extra)
    files = [f for f in files if f.resolve() != output]
    files = sorted(files)

    compression = detect_compression(output)

    with open_tar_for_write(
        output, compression, archive_config.compression_level
    ) as tf:
        add_files_to_tar(tf, root, files)
        if bundle_packages:
            add_packages_to_tar(tf, bundle_packages)

    return output


def add_files_to_tar(tf: tarfile.TarFile, root: Path, files: list[Path]) -> None:
    """Add workspace *files* to the tar, using paths relative to *root*."""
    for path in files:
        arcname = path.relative_to(root).as_posix()
        tf.add(str(path), arcname=arcname)


def add_packages_to_tar(tf: tarfile.TarFile, packages: list[Path]) -> None:
    """Add conda package archives under the ``packages/`` archive prefix."""
    for pkg in packages:
        arcname = f"packages/{pkg.name}"
        tf.add(str(pkg), arcname=arcname)


def validate_tar_member(member: tarfile.TarInfo, target: Path) -> None:
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
        resolved_link = target.joinpath(
            *member_path.parent.parts,
            *link_target.parts,
        ).resolve()
        try:
            resolved_link.relative_to(target.resolve())
        except ValueError:
            raise ArchivePathTraversalError(member.name)


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
    target.mkdir(parents=True, exist_ok=True)

    with open_tar(archive_path) as tf:
        members = tf.getmembers()
        for member in members:
            validate_tar_member(member, target)
        if hasattr(tarfile, "data_filter"):
            tf.extractall(path=target, members=members, filter="data")
        else:
            tf.extractall(path=target, members=members)

    return target


def parse_lockfile_packages(lockfile_path: Path) -> list[dict]:
    """Parse the ``packages`` list from a conda lockfile."""
    data = load_yaml(lockfile_path)
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
    lockfile_path: Path,
    cache_dirs: list[Path],
) -> list[Path]:
    """Locate conda packages referenced by the lockfile in local caches.

    Raises :class:`ArchiveError` if any package is missing from all caches.
    """
    packages_data = parse_lockfile_packages(lockfile_path)
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


def build_hash_index(lockfile_path: Path) -> dict[str, str]:
    """Build a filename-to-SHA256 mapping from lockfile package entries."""
    packages_data = parse_lockfile_packages(lockfile_path)
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
    lockfile_path: Path,
) -> None:
    """Verify SHA256 hashes of *packages* against the lockfile.

    Raises :class:`ArchiveHashMismatchError` on the first mismatch.
    """
    expected = build_hash_index(lockfile_path)

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


def prime_package_cache(
    extracted_dir: Path,
    cache_dir: Path,
    *,
    verified: bool = False,
) -> int:
    """Copy bundled packages from an extracted archive into the conda cache.

    Only copies packages after the archive has a verified receipt or a
    verified workspace attestation. Package SHA256 hashes are still verified
    against the extracted lockfile before copying.
    Returns the number of packages added to the cache.
    """
    packages_dir = extracted_dir / "packages"
    if not packages_dir.is_dir():
        return 0

    packages = sorted(
        path
        for suffix in CONDA_PACKAGE_SUFFIXES
        for path in packages_dir.glob(f"*{suffix}")
    )
    if not packages:
        return 0
    if not verified:
        raise ArchiveError(
            "Cannot prime package cache from unverified archive packages.",
            hints=[
                "Verify the archive with a receipt or attestation before cache"
                " priming.",
                "Use 'conda workspace unarchive --receipt ...' or"
                " 'conda workspace unarchive --verify', or extract without cache"
                " priming.",
            ],
        )

    lockfile = extracted_dir / "conda.lock"
    if not lockfile.is_file():
        raise ArchiveError(
            "Cannot prime package cache: bundled packages require conda.lock.",
            hints=[
                "Extract the archive without cache priming using --no-install,",
                "or rebuild the archive with its lockfile included.",
            ],
        )

    verify_package_hashes(packages, lockfile)

    cache_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for pkg in packages:
        dest = cache_dir / pkg.name
        if not dest.exists():
            shutil.copy2(pkg, dest)
            count += 1

    return count


def inspect_archive(archive_path: Path) -> dict[str, object]:
    """Return metadata about an archive without extracting it."""
    with open_tar(archive_path) as tf:
        names = set(tf.getnames())

    package_members = [
        n
        for n in names
        if n.startswith("packages/") and n.endswith(CONDA_PACKAGE_SUFFIXES)
    ]

    return {
        "has_manifest": bool(names & MANIFEST_FILENAMES),
        "has_lockfile": "conda.lock" in names,
        "has_packages": len(package_members) > 0,
        "package_count": len(package_members),
    }
