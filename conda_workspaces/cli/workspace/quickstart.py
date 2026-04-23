"""``conda workspace quickstart`` — orchestrate init + add + install + shell.

``quickstart`` is deliberately free of business logic: it is a thin
composition of :func:`execute_init`, :func:`execute_add`,
:func:`execute_install`, and :func:`execute_shell`.  Each sub-handler
owns its own error handling, dry-run semantics, and conda integration;
``quickstart`` just builds the right :class:`argparse.Namespace` for
each one and stitches their outputs together.

The one concession to user experience lives at the ``--copy`` / ``--clone``
path: when the user points quickstart at an existing workspace, we
delegate to :meth:`ManifestParser.copy_manifest` (which walks the
source with :func:`manifests.detect_workspace_file` when given a
directory) to copy whichever manifest — ``conda.toml`` /
``pixi.toml`` / ``pyproject.toml`` — lives there into the current
directory and skip the ``init`` step entirely.  Any ``--format``
value is ignored with a warning in that case — the copied manifest
already dictates the format.

The ``--json`` path is self-contained: we swallow sub-handler console
output and emit a single structured result at the end, mirroring the
shape other workspace commands use so callers can pipe the output
without guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console

from ...exceptions import (
    ManifestExistsError,
    QuickstartCopyError,
    WorkspaceNotFoundError,
)
from ...manifests.base import ManifestParser
from .add import execute_add
from .init import execute_init
from .install import execute_install
from .shell import execute_shell


def execute_quickstart(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Run the init -> add -> install -> shell pipeline."""
    if console is None:
        console = Console(highlight=False)

    dry_run: bool = args.dry_run
    json_output: bool = args.json
    no_shell: bool = args.no_shell or json_output
    copy_from: Path | None = args.copy_from
    specs: list[str] = list(args.specs or [])
    env_name: str = args.environment or "default"
    fmt: str = args.manifest_format or "conda"

    workspace_root = Path.cwd()

    # Global prompt / output flags every sub-handler must see
    # (``--json`` / ``--dry-run`` / ``--yes`` / ``-v`` / ``-q`` /
    # ``--debug`` / ``--trace``) so terminal output and machine-readable
    # output stay coherent across the pipeline.  ``add_output_and_prompt_options``
    # on the parent parser guarantees each attribute exists.
    prompt_keys = ("json", "yes", "dry_run", "quiet", "verbose", "debug", "trace")

    def with_prompts(**kwargs: object) -> argparse.Namespace:
        """Build a sub-handler ``Namespace`` from *kwargs* + ``prompt_keys``."""
        ns = argparse.Namespace(**kwargs)
        for key in prompt_keys:
            setattr(ns, key, getattr(args, key))
        return ns

    if copy_from is not None:
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
            manifest = ManifestParser.resolve_source(copy_from)
            manifest_path = workspace_root / manifest.name
            if manifest_path.exists():
                raise ManifestExistsError(manifest_path)
            if dry_run:
                console.print(
                    f"[bold blue]Would copy[/bold blue] [bold]{manifest}[/bold]"
                    f" -> [bold]{manifest_path}[/bold]"
                )
            else:
                ManifestParser.copy_manifest(copy_from, workspace_root)
                console.print(
                    f"[bold cyan]Copied[/bold cyan] [bold]{manifest.name}[/bold]"
                    f" from [bold]{manifest.parent}[/bold]"
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
        manifest_path = ManifestParser.for_format(fmt).manifest_path(workspace_root)
    else:
        execute_init(
            with_prompts(
                file=None,
                manifest_format=args.manifest_format,
                name=args.name,
                channels=args.channels,
                platforms=args.platforms,
            )
        )
        manifest_path = ManifestParser.for_format(fmt).manifest_path(workspace_root)

    if dry_run:
        # No manifest on disk yet; skip add/install side effects.
        pass
    elif specs:
        execute_add(
            with_prompts(
                file=None,
                specs=list(specs),
                environment=None,
                feature=None,
                pypi=False,
                no_install=False,
                no_lockfile_update=False,
                force_reinstall=args.force_reinstall,
            )
        )
    else:
        execute_install(
            with_prompts(
                file=None,
                environment=args.environment,
                force_reinstall=args.force_reinstall,
                locked=args.locked,
                frozen=args.frozen,
            )
        )

    shell_spawned = False
    if not no_shell and not dry_run:
        execute_shell(
            with_prompts(
                file=None,
                environment=env_name,
                cmd=None,
            )
        )
        shell_spawned = True

    if json_output:
        payload = {
            "workspace": str(workspace_root),
            "environment": env_name,
            "manifest": manifest_path.name if manifest_path is not None else None,
            "specs_added": specs,
            "shell_spawned": shell_spawned,
        }
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
    elif not dry_run:
        console.print(
            "\n[bold green]Workspace ready[/bold green] in"
            f" [bold]{workspace_root}[/bold]"
        )

    return 0


__all__ = ["execute_quickstart"]
