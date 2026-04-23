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
from typing import TYPE_CHECKING

from rich.console import Console

from ...exceptions import CondaWorkspacesError
from ...manifests import detect_workspace_file
from .add import execute_add
from .init import execute_init
from .install import execute_install
from .shell import execute_shell

if TYPE_CHECKING:
    from collections.abc import Iterable


#: Keys forwarded from ``quickstart`` to the respective sub-handler.
_INIT_KEYS: tuple[str, ...] = ("manifest_format", "name", "channels", "platforms")
_INSTALL_KEYS: tuple[str, ...] = (
    "environment",
    "force_reinstall",
    "locked",
    "frozen",
)
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


def _forward(
    args: argparse.Namespace,
    keys: Iterable[str],
    **extras: object,
) -> argparse.Namespace:
    """Build a sub-handler ``Namespace`` by copying *keys* and ``_PROMPT_KEYS``.

    *extras* overlay the copied values so callers can pin handler-specific
    defaults (e.g. ``file=None`` for init, ``cmd=None`` for shell).
    """
    ns = argparse.Namespace()
    for key in (*keys, *_PROMPT_KEYS):
        setattr(ns, key, getattr(args, key, None))
    for key, value in extras.items():
        setattr(ns, key, value)
    return ns


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
        execute_init(_forward(args, _INIT_KEYS, file=None))
        manifest_path = _guess_manifest_path(workspace_root, args)

    common_file = getattr(args, "file", None)
    if specs:
        execute_add(
            _forward(
                args,
                (),
                specs=list(specs),
                environment=None,
                feature=None,
                pypi=False,
                no_install=False,
                no_lockfile_update=False,
                force_reinstall=bool(getattr(args, "force_reinstall", False)),
                file=common_file,
            )
        )
    else:
        execute_install(_forward(args, _INSTALL_KEYS, file=common_file))

    shell_spawned = False
    if not no_shell and not dry_run:
        execute_shell(
            _forward(
                args,
                (),
                environment=env_name,
                cmd=None,
                file=common_file,
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
