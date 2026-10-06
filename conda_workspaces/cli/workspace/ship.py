"""Build native launchers with the optional conda-ship builder."""

from __future__ import annotations

import subprocess
import sys
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ...exceptions import CondaWorkspacesError, LockfileNotFoundError
from ...lockfile import (
    CondaLockLoader,
    check_lockfile_satisfiability,
    load_lockfile_path,
    lockfile_path,
)
from ...models import LockfileStatus
from ...resolver import resolve_environment
from .. import status
from . import workspace_context_from_args

if TYPE_CHECKING:
    import argparse


def execute_ship(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Build a launcher from an existing, current workspace lockfile."""
    config, ctx = workspace_context_from_args(args)
    resolved = resolve_environment(config, args.environment)
    platform = config.platform_subdir(args.platform)
    if platform != args.platform or any(
        name != platform and config.platform_subdir(name) == platform
        for name in resolved.platforms
    ):
        raise CondaWorkspacesError(
            "Shipping named platform variants is not supported by conda-ship. "
            "Use an environment with an ordinary conda platform."
        )
    resolved = resolve_environment(config, args.environment, platform)
    for name, dependency in resolved.pypi_dependencies.items():
        if dependency.path is not None or dependency.git or dependency.url:
            raise CondaWorkspacesError(
                f"Cannot ship dependency '{name}' from a local path, Git, "
                "or URL source.",
                hints=["Package the application as a conda package and lock it first."],
            )
    if resolved.pypi_dependencies:
        try:
            import_module("conda_pypi.translate").pypi_to_conda_name
        except (ImportError, AttributeError) as exc:
            raise CondaWorkspacesError(
                "conda-pypi is required to check the locked PyPI requirements.",
                hints=[
                    "Install it with: conda install -n base conda-forge::conda-pypi"
                ],
            ) from exc

    manifest = Path(config.manifest_path)
    lock = lockfile_path(ctx)
    if not lock.is_file():
        raise LockfileNotFoundError(args.environment, lock)
    try:
        data = load_lockfile_path(lock)
        current = check_lockfile_satisfiability(
            config, data, platform, environment=args.environment
        )
        if current.status != LockfileStatus.UP_TO_DATE:
            raise CondaWorkspacesError(
                f"Cannot ship from an outdated conda.lock: {current.reason}",
                hints=["Run `conda workspace lock` before building the launcher."],
            )
        platforms = CondaLockLoader(lock, data=data).platforms_for(args.environment)
        if platform not in platforms:
            raise CondaWorkspacesError(
                f"The lockfile does not cover environment '{args.environment}' "
                f"on platform '{platform}'."
            )
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        raise CondaWorkspacesError(f"Cannot ship from conda.lock: {exc}") from exc

    output = args.output.expanduser().absolute()
    requirement = (
        "Install conda-ship with explicit input selection support "
        "(--manifest, --source-lock, --source-environment) in the environment "
        "that runs conda. Released conda-ship 0.10.0 does not support these options."
    )
    if find_spec("conda_ship") is None:
        raise CondaWorkspacesError("conda-ship is not installed.", hints=[requirement])

    command = [sys.executable, "-m", "conda_ship.cli", "build"]
    try:
        help_result = subprocess.run(
            [*command, "--help"], capture_output=True, text=True, check=False
        )
        if help_result.returncode:
            raise CondaWorkspacesError(
                f"Cannot run conda-ship: {help_result.stderr.strip()}",
                hints=[requirement],
            )
        if not all(
            option in help_result.stdout.split()
            for option in ("--manifest", "--source-lock", "--source-environment")
        ):
            raise CondaWorkspacesError(
                "This conda-ship builder lacks explicit input selection support.",
                hints=[requirement],
            )
        command.extend(
            [
                "--manifest",
                str(manifest),
                "--source-lock",
                str(lock),
                "--source-environment",
                args.environment,
                "--platform",
                platform,
                "--out-dir",
                str(output),
            ]
        )
        if args.delegate_executable is not None:
            command.extend(["--delegate-executable", args.delegate_executable])
        if args.artifact_layout is not None:
            command.extend(["--artifact-layout", args.artifact_layout])
        if args.dry_run:
            command.append("--dry-run")
        result = subprocess.run(
            command, stdout=sys.stderr, stderr=sys.stderr, check=False
        )
    except OSError as exc:
        raise CondaWorkspacesError(f"Cannot run conda-ship: {exc}") from exc
    if result.returncode:
        return result.returncode if result.returncode > 0 else 128 - result.returncode

    if console is None:
        console = Console(highlight=False)
    if args.json:
        console.print_json(
            data={
                "success": True,
                "environment": args.environment,
                "platform": platform,
                "output": str(output),
                "dry_run": bool(args.dry_run),
            }
        )
    else:
        status.message(
            console, "Would write" if args.dry_run else "Wrote", "launcher", str(output)
        )
    return 0
