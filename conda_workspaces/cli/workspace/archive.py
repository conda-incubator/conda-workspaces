"""``conda workspace archive`` and ``conda workspace unarchive``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ...archive import (
    ARCHIVE_SUFFIXES,
    collect_bundle_packages,
    create_archive,
    extract_archive,
    inspect_archive,
    prime_package_cache,
    verify_package_hashes,
)
from ...exceptions import ArchiveError
from ...lockfile import lockfile_path as _lockfile_path
from ...models import ArchiveConfig
from .. import status
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse


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
        generate_lockfile(ctx, resolved_envs)
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

    bundle_packages = None
    if args.bundle:
        from conda.base.context import context as conda_context

        lockfile = _lockfile_path(ctx)
        if not lockfile.is_file():
            raise ArchiveError(
                "Cannot bundle packages: no conda.lock found.",
                hints=["Run 'conda workspace lock' first."],
            )
        cache_dirs = [Path(d) for d in conda_context.pkgs_dirs]
        bundle_packages = collect_bundle_packages(lockfile, cache_dirs)
        verify_package_hashes(bundle_packages, lockfile)

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

    create_archive(ctx.root, output, archive_config, bundle_packages=bundle_packages)

    status.message(console, "Created", "archive", str(output))
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

    target: Path | None = args.target
    if target is None:
        stem = archive_path.name
        for suffix in ARCHIVE_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        target = Path.cwd() / stem

    info = inspect_archive(archive_path)

    if not info["has_manifest"]:
        raise ArchiveError(
            "Not a workspace archive: no manifest found.",
            hints=["This does not appear to be a conda workspace archive."],
        )

    status.message(
        console,
        "Extracting",
        "archive",
        str(archive_path.name),
        style="bold blue",
        ellipsis=True,
    )

    extract_archive(archive_path, target)

    status.message(console, "Extracted", "archive", str(target))

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

    return 0
