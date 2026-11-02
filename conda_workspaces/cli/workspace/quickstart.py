"""``conda workspace quickstart`` — create or restore an installed workspace.

Package specs compose init, add, install, and shell. Archive URLs reuse
:class:`WorkspaceArchive` retrieval, verification, and locked installation,
then open a shell in the restored workspace. Downloads and receipts follow
the same policy as ``unarchive``.

``--copy`` / ``--clone`` copy a local workspace manifest through
:meth:`ManifestParser.copy_manifest` and skip init. Archive restoration
also preserves the lockfile and project files in a new directory.

Quickstart owns the JSON result and suppresses nested handlers' output.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import ExitStack, nullcontext, redirect_stdout
from pathlib import Path
from urllib.parse import unquote, urlsplit

from conda.base.constants import DEFAULT_JSON_REPORTER_BACKEND
from conda.base.context import context as conda_context
from conda.common.compat import on_win
from conda.exceptions import ArgumentError, CondaError
from conda.utils import quote_for_shell
from rich.console import Console

from ...archive import ARCHIVE_SUFFIXES, CONDA_PACKAGE_SUFFIXES, WorkspaceArchive
from ...context import WorkspaceContext
from ...exceptions import (
    CondaWorkspacesError,
    ManifestExistsError,
    QuickstartCopyError,
    WorkspaceNotFoundError,
)
from ...manifests.base import ManifestParser
from .. import status
from .add import execute_add
from .archive import install_from_archive_cli
from .init import execute_init, resolve_init_channels
from .install import execute_install
from .shell import execute_shell


def execute_quickstart(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Create or restore a workspace, install it, and optionally open a shell."""
    if console is None:
        console = Console(highlight=False)

    dry_run: bool = args.dry_run
    json_output = args.json or conda_context.console == DEFAULT_JSON_REPORTER_BACKEND
    no_shell: bool = args.no_shell or json_output

    # Nested handlers stay in human-output mode. Only quickstart emits JSON.
    if json_output:
        console = Console(
            file=io.StringIO(),
            highlight=False,
            force_terminal=False,
            no_color=True,
        )
    copy_from: Path | None = args.copy_from
    if args.override_channels and not args.channel:
        raise ArgumentError(
            "At least one -c / --channel flag must be supplied when using "
            "--override-channels."
        )
    specs: list[str] = list(args.specs or [])
    archive_source: str | None = None
    for spec in specs:
        if WorkspaceArchive.is_remote_source(spec):
            try:
                name = unquote(urlsplit(spec).path.rsplit("/", 1)[-1], errors="strict")
            except (ValueError, UnicodeError):
                raise ArgumentError(
                    f"Invalid source URL: {WorkspaceArchive.source_label(spec)}"
                ) from None
            if getattr(args, "archive", False) or (
                name.endswith(ARCHIVE_SUFFIXES)
                and not name.endswith(CONDA_PACKAGE_SUFFIXES)
            ):
                archive_source = spec
            elif not name.endswith(CONDA_PACKAGE_SUFFIXES):
                raise ArgumentError(
                    f"Unrecognized source URL: {WorkspaceArchive.source_label(spec)}. "
                    "Use a workspace archive URL ending in .tar.zst, .tar.zstd, "
                    ".tar.gz, or .tgz, or a package URL ending in .conda or .tar.bz2. "
                    "For a .tar.bz2 workspace archive, pass --archive."
                )
    if archive_source is not None:
        if len(specs) != 1:
            raise ArgumentError(
                "An archive URL cannot be combined with package specs or other URLs."
            )
        conflicts = {
            "--copy/--clone": copy_from,
            "--file": getattr(args, "manifest_file", None),
            "--format": args.manifest_format,
            "--name": args.name,
            "--platform": args.platforms,
            "--channel": args.channel,
            "--override-channels": args.override_channels,
            "--force-reinstall": args.force_reinstall,
            "--frozen": args.frozen,
        }
        selected = [option for option, value in conflicts.items() if value]
        if selected:
            raise ArgumentError(
                "Archive quickstart preserves the published workspace and lockfile. "
                f"Remove incompatible options: {', '.join(selected)}."
            )
        if getattr(args, "require_sha256", False) and not getattr(
            args, "receipt", None
        ):
            raise ArgumentError("--require-sha256 requires --receipt.")
    elif any(
        getattr(args, option, None)
        for option in (
            "archive",
            "target",
            "receipt",
            "require_sha256",
            "allow_large_archive",
        )
    ):
        raise ArgumentError(
            "Archive options require one positional workspace archive URL. "
            "For a .tar.bz2 workspace archive, pass --archive."
        )
    if archive_source is None and specs and (args.locked or args.frozen):
        raise ArgumentError(
            "Positional package specs cannot be combined with --locked or --frozen. "
            "Omit the lock mode to create or update conda.lock while adding specs."
        )
    env_name: str = args.environment or "default"
    WorkspaceContext.validate_environment_name(env_name)
    fmt: str = args.manifest_format or "conda"

    workspace_root = Path.cwd()
    source_manifest: Path | None = None

    # Forward prompt flags but keep JSON output under quickstart's control.
    prompt_keys = ("yes", "dry_run", "quiet", "verbosity", "debug", "trace")

    def with_prompts(**kwargs: object) -> argparse.Namespace:
        """Build a sub-handler ``Namespace`` from *kwargs* + ``prompt_keys``."""
        ns = argparse.Namespace(**kwargs)
        for key in prompt_keys:
            setattr(ns, key, getattr(args, key))
        return ns

    archive_result = None
    recovery: list[str] = []
    recovery_hint = ["Run these commands in Command Prompt:"] if on_win else []
    return_code = 0
    receipt_label = None
    artifact = None
    if archive_source is not None:
        receipt = getattr(args, "receipt", None)
        source_label = WorkspaceArchive.source_label(archive_source)
        receipt_label = (
            source_label + ".receipt.json"
            if receipt is True
            else WorkspaceArchive.source_label(receipt)
            if receipt
            else None
        )
        with (
            redirect_stdout(io.StringIO()) if json_output else nullcontext(),
            WorkspaceArchive.open_source(
                archive_source,
                receipt=receipt,
                allow_large_archive=getattr(args, "allow_large_archive", False),
            ) as archive,
        ):
            artifact = archive.path.name
            target = getattr(args, "target", None)
            workspace_root = (
                Path(target).expanduser()
                if target is not None
                else archive.default_target()
            ).absolute()
            recovery = [
                quote_for_shell("cd", *(["/d"] if on_win else []), str(workspace_root)),
                quote_for_shell(
                    "conda", "workspace", "install", "--locked", "-e", env_name
                ),
                quote_for_shell("conda", "workspace", "shell", "-e", env_name),
            ]
            handler = install_from_archive_cli(console)

            def install(
                workspace: Path,
                environment: str | None,
                prefix: Path | None,
                target_prefix_override: str | None,
            ) -> int:
                try:
                    return handler(
                        workspace, environment, prefix, target_prefix_override
                    )
                except (CondaError, OSError) as exc:
                    raise CondaWorkspacesError(
                        f"Workspace restored to '{workspace}',"
                        f" but installation failed: {exc}",
                        hints=[*recovery_hint, *recovery],
                    ) from exc

            status.message(
                console,
                "Would restore" if dry_run else "Restoring",
                "archive",
                source_label,
            )
            archive_result = archive.install(
                target=workspace_root,
                environment=env_name,
                require_sha256=getattr(args, "require_sha256", False),
                install_handler=install,
                dry_run=dry_run,
                allow_large_archive=getattr(args, "allow_large_archive", False),
            )
            workspace_root = archive_result.target
            manifest_path = (
                WorkspaceArchive.resolve_extracted_manifest(workspace_root)
                if not dry_run
                else None
            )
            return_code = archive_result.return_code
            if archive_result.verified:
                status.message(console, "Verified", "receipt", str(receipt_label))
    elif copy_from is not None:
        if args.manifest_format not in (None, "conda"):
            console.print(
                "[bold yellow]Warning[/bold yellow] --format is ignored when"
                " --copy/--clone is used; the copied manifest dictates the"
                " format."
            )
        # Copy the foreign manifest into the workspace, translating the
        # various "source missing / already exists" failures into one
        # uniform ``QuickstartCopyError`` so quickstart's error surface
        # stays consistent.  The real work lives on :class:`ManifestParser`;
        # we only layer dry-run preview + Rich output on top.
        try:
            source_manifest = ManifestParser.resolve_source(copy_from)
            manifest_path = workspace_root / source_manifest.name
            if manifest_path.exists() or manifest_path.is_symlink():
                raise ManifestExistsError(manifest_path)
            if dry_run:
                console.print(
                    f"[bold blue]Would copy[/bold blue] [bold]{source_manifest}[/bold]"
                    f" -> [bold]{manifest_path}[/bold]"
                )
            else:
                ManifestParser.copy_manifest(copy_from, workspace_root)
                console.print(
                    f"[bold cyan]Copied[/bold cyan] [bold]{source_manifest.name}[/bold]"
                    f" from [bold]{source_manifest.parent}[/bold]"
                )
        except FileNotFoundError as exc:
            raise QuickstartCopyError(
                f"--copy source '{copy_from}' does not exist.",
                hints=["Pass an existing workspace directory or manifest file."],
            ) from exc
        except WorkspaceNotFoundError as exc:
            raise QuickstartCopyError(
                f"--copy source '{copy_from}' does not contain a workspace manifest.",
                hints=list(exc.hints),
            ) from exc
        except ManifestExistsError as exc:
            raise QuickstartCopyError(
                f"'{exc.path}' already exists; refusing to overwrite.",
                hints=["Remove the existing manifest or pick a different directory."],
            ) from exc
    elif dry_run:
        console.print(
            "[bold blue]Would create[/bold blue] workspace manifest in"
            f" [bold]{workspace_root}[/bold]"
        )
        parser = ManifestParser.for_format_alias(fmt)
        manifest_path = parser.manifest_path(workspace_root)
        if manifest_path.is_symlink():
            raise ManifestExistsError(manifest_path)
    else:
        with redirect_stdout(io.StringIO()) if json_output else nullcontext():
            execute_init(
                with_prompts(
                    manifest_file=None,
                    manifest_format=fmt,
                    name=args.name,
                    platforms=args.platforms,
                ),
                console=console,
            )
        parser = ManifestParser.for_format_alias(fmt)
        manifest_path = parser.manifest_path(workspace_root)

    if archive_source is None:
        assert manifest_path is not None
        with ExitStack() as stack:
            handler_manifest_path: Path | None = None
            if dry_run:
                temporary = stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="conda-workspaces-quickstart-")
                )
                staged_root = Path(temporary)
                if source_manifest is not None:
                    handler_manifest_path = ManifestParser.copy_manifest(
                        source_manifest,
                        staged_root,
                    )
                else:
                    parser = ManifestParser.for_format_alias(fmt)
                    handler_manifest_path = parser.manifest_path(staged_root)
                    if manifest_path.exists():
                        shutil.copy2(manifest_path, handler_manifest_path)
                    parser.write_workspace_stub(
                        staged_root,
                        args.name or workspace_root.name,
                        resolve_init_channels(),
                        args.platforms or [conda_context.subdir],
                    )

            with redirect_stdout(io.StringIO()) if json_output else nullcontext():
                if specs:
                    execute_add(
                        with_prompts(
                            manifest_file=handler_manifest_path,
                            validation_manifest_path=manifest_path if dry_run else None,
                            specs=list(specs),
                            environment=env_name,
                            feature=None,
                            pypi=False,
                            no_install=False,
                            no_lockfile_update=False,
                            force_reinstall=args.force_reinstall,
                        ),
                        console=console,
                    )
                else:
                    execute_install(
                        with_prompts(
                            manifest_file=handler_manifest_path,
                            validation_manifest_path=manifest_path if dry_run else None,
                            environment=args.environment,
                            force_reinstall=args.force_reinstall,
                            locked=args.locked,
                            frozen=args.frozen,
                        ),
                        console=console,
                    )

    shell_spawned = False
    installed = not dry_run and return_code == 0
    if not no_shell and installed:
        original_cwd = Path.cwd()
        try:
            if archive_source is not None:
                os.chdir(workspace_root)
            shell_code = execute_shell(
                with_prompts(
                    manifest_file=manifest_path if archive_source is not None else None,
                    environment=env_name,
                    cmd=None,
                )
            )
        except (CondaError, OSError, ImportError) as exc:
            if archive_source is None:
                raise
            raise CondaWorkspacesError(
                f"Workspace installed in '{workspace_root}',"
                f" but the shell failed: {exc}",
                hints=[*recovery_hint, recovery[0], recovery[2]],
            ) from exc
        finally:
            if archive_source is not None:
                os.chdir(original_cwd)
        shell_spawned = True
        if archive_source is not None:
            return_code = shell_code
            recovery = [recovery[0], recovery[2]]

    if archive_source is not None and return_code and not json_output:
        phase = (
            "installed, but the shell failed"
            if installed
            else "restored, but installation failed"
        )
        console.print(f"Workspace {phase} in '{workspace_root}'.", markup=False)
        for command in [*recovery_hint, *recovery]:
            console.print(command, markup=False, soft_wrap=True)

    if json_output:
        payload = {
            "workspace": str(workspace_root),
            "environment": env_name,
            "manifest": manifest_path.name if manifest_path is not None else None,
            "specs_added": specs if archive_source is None else [],
            "shell_spawned": shell_spawned,
        }
        if archive_result is not None:
            payload.update(
                success=return_code == 0,
                source=source_label,
                artifact=artifact,
                receipt=receipt_label,
                verified=archive_result.verified,
                dry_run=dry_run,
                installed=installed,
                primed_packages=archive_result.primed_packages,
                cache_priming_skipped=archive_result.cache_priming_skipped,
                return_code=return_code,
                recovery=recovery if return_code else [],
                recovery_shell=("cmd.exe" if on_win else "sh") if return_code else None,
            )
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
    elif not dry_run and return_code == 0:
        console.print(
            "\n[bold green]Workspace ready[/bold green] in"
            f" [bold]{workspace_root}[/bold]"
        )

    return return_code


__all__ = ["execute_quickstart"]
