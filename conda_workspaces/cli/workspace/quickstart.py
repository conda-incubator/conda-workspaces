"""``conda workspace quickstart`` — orchestrate init + add + install + shell.

``quickstart`` is deliberately free of business logic: it is a thin
composition of :func:`execute_init`, :func:`execute_add`,
:func:`execute_install`, and :func:`execute_shell`.  Each sub-handler
owns its own error handling, dry-run semantics, and conda integration;
``quickstart`` just builds the right :class:`argparse.Namespace` for
each one and stitches their outputs together.

The one concession to user experience lives at the ``--copy`` / ``--clone``
path: when the user points quickstart at an existing workspace, we
copy whichever manifest (``conda.toml`` / ``pixi.toml`` /
``pyproject.toml``) :func:`manifests.detect_workspace_file` finds there
into the current directory and skip the ``init`` step entirely.  Any
``--format`` value is ignored with a warning in that case — the copied
manifest already dictates the format.

The ``--json`` path is self-contained: we swallow sub-handler console
output and emit a single structured result at the end, mirroring the
shape other workspace commands use so callers can pipe the output
without guessing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from rich.console import Console

from ...exceptions import CondaWorkspacesError
from ...manifests import detect_workspace_file
from .add import execute_add
from .init import execute_init
from .install import execute_install
from .shell import execute_shell

#: Global prompt / output flags every sub-handler sees (``--json``,
#: ``--dry-run``, ``--yes``, ``-v``/``-q``, ``--debug``, ``--trace``).
#: Forwarded verbatim through :func:`execute_quickstart.with_prompts`.
_PROMPT_KEYS: tuple[str, ...] = (
    "json",
    "yes",
    "dry_run",
    "quiet",
    "verbose",
    "debug",
    "trace",
)


class QuickstartCopyError(CondaWorkspacesError):
    """``--copy`` / ``--clone`` pointed at something we cannot use."""


def execute_quickstart(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Run the init -> add -> install -> shell pipeline."""
    if console is None:
        console = Console(highlight=False)

    dry_run = bool(getattr(args, "dry_run", False))
    json_output = bool(getattr(args, "json", False))
    no_shell = bool(getattr(args, "no_shell", False)) or json_output
    copy_from: Path | None = getattr(args, "copy_from", None)
    specs: list[str] = list(getattr(args, "specs", None) or [])
    env_name = getattr(args, "environment", "default") or "default"

    workspace_root = Path.cwd()
    if getattr(args, "file", None):
        workspace_root = Path(args.file).resolve().parent

    common_file = getattr(args, "file", None)

    def with_prompts(**kwargs: object) -> argparse.Namespace:
        """Build a sub-handler ``Namespace`` from *kwargs* + ``_PROMPT_KEYS``.

        Each sub-handler call site lists the flags it cares about
        explicitly; this closure fills in the global prompt/output
        flags (``--json`` / ``--dry-run`` / etc.) that every handler
        must see so terminal output and machine-readable output stay
        coherent across the pipeline.
        """
        ns = argparse.Namespace(**kwargs)
        for key in _PROMPT_KEYS:
            setattr(ns, key, getattr(args, key, None))
        return ns

    if copy_from is not None:
        if getattr(args, "manifest_format", None) not in (None, "conda"):
            console.print(
                "[bold yellow]Warning[/bold yellow] --format is ignored when"
                " --copy/--clone is used; the copied manifest dictates the"
                " format."
            )
        manifest_path = _copy_manifest(
            copy_from,
            workspace_root,
            dry_run=dry_run,
            console=console,
        )
    elif dry_run:
        console.print(
            "[bold blue]Would create[/bold blue] workspace manifest in"
            f" [bold]{workspace_root}[/bold]"
        )
        manifest_path = _guess_manifest_path(workspace_root, args)
    else:
        execute_init(
            with_prompts(
                file=None,
                manifest_format=getattr(args, "manifest_format", None),
                name=getattr(args, "name", None),
                channels=getattr(args, "channels", None),
                platforms=getattr(args, "platforms", None),
            )
        )
        manifest_path = _guess_manifest_path(workspace_root, args)

    if specs:
        execute_add(
            with_prompts(
                file=common_file,
                specs=list(specs),
                environment=None,
                feature=None,
                pypi=False,
                no_install=False,
                no_lockfile_update=False,
                force_reinstall=bool(getattr(args, "force_reinstall", False)),
            )
        )
    else:
        execute_install(
            with_prompts(
                file=common_file,
                environment=getattr(args, "environment", None),
                force_reinstall=getattr(args, "force_reinstall", None),
                locked=getattr(args, "locked", None),
                frozen=getattr(args, "frozen", None),
            )
        )

    shell_spawned = False
    if not no_shell and not dry_run:
        execute_shell(
            with_prompts(
                file=common_file,
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


def _copy_manifest(
    source: Path,
    dest_dir: Path,
    *,
    dry_run: bool,
    console: Console,
) -> Path:
    """Copy the workspace manifest from *source* into *dest_dir*.

    *source* may be either a directory containing a manifest or a path
    to the manifest itself.  The returned path is the manifest location
    inside *dest_dir*.
    """
    if not source.exists():
        raise QuickstartCopyError(
            f"--copy source '{source}' does not exist.",
            hints=["Pass an existing workspace directory or manifest file."],
        )

    if source.is_dir():
        try:
            manifest = detect_workspace_file(source)
        except CondaWorkspacesError as exc:
            raise QuickstartCopyError(
                f"--copy source '{source}' does not contain a workspace manifest.",
                hints=list(getattr(exc, "hints", None) or []),
            ) from exc
    else:
        manifest = source

    target = dest_dir / manifest.name
    if target.exists():
        raise QuickstartCopyError(
            f"'{target}' already exists; refusing to overwrite.",
            hints=["Remove the existing manifest or pick a different directory."],
        )

    if dry_run:
        console.print(
            f"[bold blue]Would copy[/bold blue] [bold]{manifest}[/bold]"
            f" -> [bold]{target}[/bold]"
        )
        return target

    shutil.copyfile(manifest, target)
    console.print(
        f"[bold cyan]Copied[/bold cyan] [bold]{manifest.name}[/bold]"
        f" from [bold]{manifest.parent}[/bold]"
    )
    return target


def _guess_manifest_path(workspace_root: Path, args: argparse.Namespace) -> Path:
    """Pick the manifest path init will (or did) write.

    Used only for the ``--json`` payload; falls back to ``conda.toml``
    when the format is unspecified so the output field is never empty.
    """
    fmt = getattr(args, "manifest_format", None) or "conda"
    if fmt == "pixi":
        return workspace_root / "pixi.toml"
    if fmt == "pyproject":
        return workspace_root / "pyproject.toml"
    return workspace_root / "conda.toml"


__all__ = [
    "QuickstartCopyError",
    "execute_quickstart",
]
