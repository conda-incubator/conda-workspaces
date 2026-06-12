"""``conda workspace archive`` and ``conda workspace unarchive``."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from os.path import expanduser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from ...archive import (
    ARCHIVE_SUFFIXES,
    collect_archive_files,
    collect_bundle_packages,
    create_archive,
    extract_archive,
    has_absolute_path_syntax,
    inspect_archive,
    prime_package_cache,
    verify_package_hashes,
)
from ...exceptions import ArchiveError
from ...lockfile import lockfile_path as _lockfile_path
from ...models import ArchiveConfig
from ...receipts import ArchiveReceipt
from .. import status
from . import workspace_context_from_args

if TYPE_CHECKING:
    from collections.abc import Callable


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


def warn_staging_prefix_references(
    console: Console,
    *,
    install_prefix: Path,
    runtime_prefix: str,
) -> None:
    """Warn when a staged install still contains the physical staging prefix."""
    matches, truncated = scan_prefix_references(install_prefix, install_prefix)
    if not matches:
        return

    console.print(
        "[bold yellow]Warning:[/bold yellow] "
        "installed files still reference the staging prefix"
    )
    console.print(f"  [dim]staging prefix:[/dim] {escape(str(install_prefix))}")
    console.print(f"  [dim]runtime prefix:[/dim] {escape(str(runtime_prefix))}")
    for path in matches:
        try:
            display_path = path.relative_to(install_prefix)
        except ValueError:
            display_path = path
        console.print(f"  [dim]- {escape(str(display_path))}[/dim]")
    if truncated:
        console.print("  [dim]additional matches omitted[/dim]")


def resolve_receipt_path(archive_path: Path, receipt: object) -> Path | None:
    """Resolve the optional ``--receipt [PATH]`` argparse value."""
    if receipt in (None, False):
        return None
    if receipt is True:
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


def ensure_verified_target_empty(target: Path) -> None:
    """Reject verified extraction into non-empty or unsafe targets."""
    if not target.exists():
        return
    if target.is_symlink():
        raise ArchiveError("Cannot verify receipt into an existing symlink target.")
    if not target.is_dir():
        raise ArchiveError(
            "Cannot verify receipt into an existing non-directory target."
        )
    try:
        target_has_files = any(target.iterdir())
    except OSError as exc:
        raise ArchiveError(
            f"Cannot inspect target before verified extraction: {target}"
        ) from exc
    if target_has_files:
        raise ArchiveError(
            "Cannot verify receipt into a non-empty target.",
            hints=["Choose an empty target directory or remove existing files first."],
        )


def extract_verified_archive(
    archive_path: Path,
    target: Path,
    receipt: ArchiveReceipt,
    *,
    require_sha256: bool = False,
) -> None:
    """Extract to a staging directory, verify, then move into *target*."""
    target = target.resolve()
    ensure_verified_target_empty(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{target.name}.verify-", dir=target.parent))
    try:
        extract_archive(archive_path, staged)
        receipt.verify_extracted(staged, require_sha256=require_sha256)
        if target.exists():
            target.rmdir()
        staged.rename(target)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise


def execute_archive(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Create a workspace archive."""
    if console is None:
        console = Console(highlight=False)

    config, ctx = workspace_context_from_args(args)

    if args.lock:
        from ...lockfile import generate_lockfile
        from ...resolver import resolve_all_environments

        resolved_envs = resolve_all_environments(config, ctx.platform)

        status.message(
            console,
            "Locking",
            "workspace",
            "environments",
            style="bold blue",
            ellipsis=True,
        )
        generate_lockfile(ctx, resolved_envs, config=config)
        status.message(console, "Updated", "lockfile", "conda.lock")

    cli_excludes = tuple(args.exclude or [])
    archive_config = ArchiveConfig(
        include=config.archive.include,
        exclude=config.archive.exclude + cli_excludes,
        compression=config.archive.compression,
        compression_level=config.archive.compression_level,
    )

    output: Path | None = args.output
    if output is None:
        name = config.name or ctx.root.name
        ext = {"zst": ".tar.zst", "gz": ".tar.gz", "bz2": ".tar.bz2"}.get(
            config.archive.compression, ".tar.zst"
        )
        output = ctx.root / f"{name}{ext}"

    receipt_path = resolve_receipt_path(
        output.resolve(), getattr(args, "receipt", None)
    )
    manifest_path = Path(config.manifest_path)
    lockfile_path = _lockfile_path(ctx)
    if receipt_path is not None:
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

        archive_members = {
            path.relative_to(ctx.root).as_posix()
            for path in collect_archive_files(ctx.root, archive_config)
            if path.resolve() != output.resolve()
        }
        required_members: dict[str, Path] = {
            "workspace manifest": manifest_path,
            "workspace lockfile": lockfile_path,
        }
        missing = []
        for label, path in required_members.items():
            try:
                archive_name = path.relative_to(ctx.root).as_posix()
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

    bundle_packages = None
    if args.bundle:
        from conda.base.context import context as conda_context

        if not lockfile_path.is_file():
            raise ArchiveError(
                "Cannot bundle packages: no conda.lock found.",
                hints=["Run 'conda workspace lock' first."],
            )
        cache_dirs = [Path(d) for d in conda_context.pkgs_dirs]
        bundle_packages = collect_bundle_packages(lockfile_path, cache_dirs)
        verify_package_hashes(bundle_packages, lockfile_path)

        status.message(
            console,
            "Bundling",
            "packages",
            str(len(bundle_packages)),
            style="bold blue",
            ellipsis=True,
        )

    status.message(
        console,
        "Creating",
        "archive",
        str(output.name),
        style="bold blue",
        ellipsis=True,
    )

    output = create_archive(
        ctx.root,
        output,
        archive_config,
        bundle_packages=bundle_packages,
    )

    status.message(console, "Created", "archive", str(output))

    if receipt_path is not None:
        receipt = ArchiveReceipt.build(
            root=ctx.root,
            archive_path=output,
            archive_config=archive_config,
            manifest_path=manifest_path,
            lockfile_path=lockfile_path,
            environment_prefixes=receipt_environment_prefixes(
                config_environments=list(config.environments),
                ctx_root=ctx.root,
                env_prefix=ctx.env_prefix,
            ),
            options={
                "bundle": bool(args.bundle),
                "lock": bool(args.lock),
                "include": list(archive_config.include),
                "exclude": list(archive_config.exclude),
                "compressionLevel": archive_config.compression_level,
            },
        )
        receipt.write(receipt_path)
        status.message(console, "Created", "receipt", str(receipt_path))
    return 0


def execute_unarchive(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Extract a workspace archive."""
    if console is None:
        console = Console(highlight=False)

    archive_path = Path(args.archive_path).resolve()
    if not archive_path.is_file():
        raise ArchiveError(f"Archive not found: {archive_path}")

    receipt_path = resolve_receipt_path(archive_path, getattr(args, "receipt", None))
    if getattr(args, "require_sha256", False) and receipt_path is None:
        raise ArchiveError("--require-sha256 requires --receipt.")

    receipt = None
    if receipt_path is not None:
        receipt = ArchiveReceipt.load(receipt_path)
        receipt.verify_archive(archive_path)
        status.message(console, "Verified", "archive", str(archive_path.name))

    target: Path | None = args.target
    if target is None:
        stem = archive_path.name
        for suffix in ARCHIVE_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        target = Path.cwd() / stem
    target = target.resolve()

    info = inspect_archive(archive_path)

    if not info["has_manifest"]:
        raise ArchiveError(
            "Not a workspace archive: no manifest found.",
            hints=["This does not appear to be a conda workspace archive."],
        )

    env_name = getattr(args, "environment", None)
    final_prefix_arg = getattr(args, "prefix", None)
    final_prefix = str(final_prefix_arg) if final_prefix_arg is not None else None
    dest = getattr(args, "dest", None)

    if final_prefix is not None and not args.install:
        raise ArchiveError(
            "--prefix requires --install.",
            hints=["Pass --install when installing to an explicit prefix."],
        )
    if dest is not None and not args.install:
        raise ArchiveError(
            "--dest requires --install.",
            hints=["Pass --install when using a staging destination."],
        )

    if args.install:
        if final_prefix is not None and not env_name:
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

    status.message(
        console,
        "Extracting",
        "archive",
        str(archive_path.name),
        style="bold blue",
        ellipsis=True,
    )

    if receipt is None:
        extract_archive(archive_path, target)
    else:
        extract_verified_archive(
            archive_path,
            target,
            receipt,
            require_sha256=getattr(args, "require_sha256", False),
        )

    status.message(console, "Extracted", "archive", str(target))
    if receipt is not None:
        status.message(console, "Verified", "receipt", str(receipt_path))

    if info["has_packages"]:
        console.print(f"  Archive includes {info['package_count']} bundled packages")
        if not args.no_install:
            from conda.base.context import context as conda_context

            cache_dir = Path(conda_context.pkgs_dirs[0])
            count = prime_package_cache(target, cache_dir)
            if count > 0:
                status.message(
                    console,
                    "Primed",
                    "packages",
                    str(count),
                    detail="into conda cache",
                )

    if args.install:
        install_prefix = Path(final_prefix) if final_prefix is not None else None
        target_prefix_override = None
        if final_prefix is not None:
            if dest is not None:
                dest = Path(dest).expanduser().resolve()
                install_prefix = dest / runtime_prefix_relative_path(final_prefix)
                target_prefix_override = final_prefix
            elif str(install_prefix) != final_prefix:
                target_prefix_override = final_prefix

        from .install import execute_install

        install_args = argparse.Namespace(
            file=str(target),
            environment=env_name,
            force_reinstall=False,
            locked=True,
            frozen=False,
            dry_run=False,
            json=False,
            prefix=install_prefix,
            target_prefix_override=target_prefix_override,
        )
        result = execute_install(install_args, console=console)
        if (
            result == 0
            and install_prefix is not None
            and target_prefix_override is not None
        ):
            warn_staging_prefix_references(
                console,
                install_prefix=install_prefix,
                runtime_prefix=target_prefix_override,
            )
        return result

    return 0
