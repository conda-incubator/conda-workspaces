"""Handler for ``conda workspace import``."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from conda.cli.common import is_active_prefix
from conda.exceptions import CondaSystemExit, DryRunExit
from conda.models.channel import Channel
from conda.reporters import confirm_yn
from conda.utils import quote_for_shell
from rich.console import Console
from rich.syntax import Syntax

from ...context import WorkspaceContext
from ...exceptions import CondaWorkspacesError, ManifestImportError
from ...importers import EnvironmentYmlImporter, find_importer
from ...lockfile import LockfileInstallPlan
from ...manifests import detect_workspace_file, find_parser
from ...manifests.toml import CondaTomlParser
from ...models import Environment, redact_channel_name, redact_url_text
from ...paths import atomic_write_text, regular_file_generation
from ...publication import WorkspacePublication
from .. import status
from . import workspace_manifest_path_from_args
from .dependencies import DependencyLocation, ensure_child_table, workspace_toml_source
from .sync import sync_environments

if TYPE_CHECKING:
    import argparse


def execute_import(args: argparse.Namespace, *, console: Console | None = None) -> int:
    """Execute the ``conda workspace import`` subcommand."""
    if console is None:
        console = Console(highlight=False)

    source: Path = args.file
    if not source.exists():
        status.print_error(console, FileNotFoundError(str(source)))
        return 1

    quiet = getattr(args, "quiet", False)
    dry_run = getattr(args, "dry_run", False)
    importer = find_importer(source)
    if not quiet:
        status.message(
            console,
            "Reading",
            "manifest",
            source.name,
            style="bold blue",
            ellipsis=True,
        )

    environment = getattr(args, "environment", None)
    if environment is not None:
        if not isinstance(importer, EnvironmentYmlImporter):
            raise ManifestImportError(
                source,
                "named environment import only supports environment.yml or "
                "environment.yaml.",
            )
        if getattr(args, "output", None) is not None:
            raise CondaWorkspacesError(
                "--output cannot be combined with --environment."
            )
        no_install = getattr(args, "no_install", False)
        no_lockfile_update = getattr(args, "no_lockfile_update", False)
        force_reinstall = getattr(args, "force_reinstall", False)
        if force_reinstall and (no_install or no_lockfile_update):
            raise CondaWorkspacesError(
                "--force-reinstall cannot be combined with --no-install or "
                "--no-lockfile-update."
            )

        imported = importer.parse_named_environment(source)
        selected_manifest_path = workspace_manifest_path_from_args(
            args,
            for_mutation=True,
        )
        manifest_path = selected_manifest_path or detect_workspace_file()
        WorkspacePublication.validate_manifest_path(manifest_path)
        parser = find_parser(manifest_path)
        original_text = parser.read_manifest_text(manifest_path)
        doc = parser.parse_toml_text_with_redacted_errors(
            original_text,
            manifest_path,
        )
        workspace_source, _ = workspace_toml_source(
            doc,
            manifest_path,
            create=True,
        )
        assert workspace_source is not None
        current = parser.parse_data_with_redacted_errors(
            doc.unwrap(),
            manifest_path,
        )
        if environment in current.environments:
            command = ["conda", "workspace"]
            if selected_manifest_path is not None:
                command.extend(("--file", str(selected_manifest_path)))
            command.extend(("install", "-e", environment))
            raise CondaWorkspacesError(
                f"Environment '{environment}' is already defined in the workspace.",
                hints=[
                    (
                        "Choose a new name or run "
                        f"'{quote_for_shell(*command)}' to synchronize the existing "
                        "environment."
                    )
                ],
            )
        current.validate_new_environment_name(environment)

        if imported.channels is not None:
            if any(channel.casefold() == "nodefaults" for channel in imported.channels):
                raise ManifestImportError(
                    source,
                    "the nodefaults channel marker cannot be represented by a "
                    "named workspace environment.",
                )
            imported_channels = [Channel(channel) for channel in imported.channels]
            if imported_channels != current.channels:
                expected = ", ".join(
                    redact_channel_name(channel) for channel in current.channels
                )
                raise ManifestImportError(
                    source,
                    "channels must match the workspace channels in the same order.",
                    hints=[f"Workspace channels: {expected or '(none)'}"],
                )
        if imported.platforms is not None and (
            len(imported.platforms) != len(current.platforms)
            or set(imported.platforms) != set(current.platforms)
        ):
            raise ManifestImportError(
                source,
                "platforms must match the workspace platform names.",
                hints=[
                    "Workspace platforms: " + (", ".join(current.platforms) or "(none)")
                ],
            )

        environments = workspace_source.get("environments")
        if environment != Environment.DEFAULT_NAME and not environments:
            ensure_child_table(workspace_source, "environments")[
                Environment.DEFAULT_NAME
            ] = []
        target, created_environment = DependencyLocation(
            environment=environment
        ).ensure_table(workspace_source)
        assert created_environment
        target["no-default-feature"] = True
        if imported.conda_dependencies:
            dependencies = ensure_child_table(target, "dependencies")
            for name, value in imported.conda_dependencies.items():
                dependencies[name] = value
        if imported.pypi_dependencies:
            pypi_dependencies = ensure_child_table(target, "pypi-dependencies")
            for name, value in imported.pypi_dependencies.items():
                pypi_dependencies[name] = value

        updated_text = tomlkit.dumps(doc)
        parser.validate_no_url_credentials(
            doc.unwrap(),
            manifest_path,
            content=updated_text,
        )
        config = parser.parse_data_with_redacted_errors(
            doc.unwrap(),
            manifest_path,
        )
        ctx = WorkspaceContext(config)
        prefix = ctx.env_prefix(environment)
        if is_active_prefix(str(prefix)):
            raise CondaWorkspacesError(
                f"Cannot replace active workspace environment '{environment}'.",
                hints=["Deactivate the environment and retry."],
            )
        if (
            LockfileInstallPlan.prefix_identity(prefix) is not None
            and not force_reinstall
        ):
            raise CondaWorkspacesError(
                f"Workspace environment prefix already exists: {prefix}",
                hints=[
                    "Remove it manually or pass --force-reinstall if it is not active."
                ],
            )

        warning_console = (
            Console(stderr=True, highlight=False)
            if getattr(args, "json", False)
            else console
        )
        if imported.name is not None and imported.name != environment:
            warning_console.print(
                status.escape_for_console(
                    "Warning: ignoring the environment.yml name in favor of "
                    f"command-line environment '{environment}'."
                ),
                style="yellow",
            )
        if imported.prefix is not None:
            warning_console.print(
                "Warning: ignoring the environment.yml prefix because workspace "
                "environments use project-local prefixes.",
                style="yellow",
            )

        publication = WorkspacePublication(
            ctx,
            manifest_path,
            original_text,
            updated_text,
            "import",
        )
        publication_context = nullcontext() if dry_run else publication.guard()
        recovery_prefix = ["conda", "workspace"]
        if selected_manifest_path is not None:
            recovery_prefix.extend(("--file", str(selected_manifest_path)))
        recovery_command = (
            quote_for_shell(*recovery_prefix, "lock")
            if no_install
            else quote_for_shell(
                *recovery_prefix,
                "install",
                "-e",
                environment,
            )
        )
        try:
            with publication_context:
                action = "Would import" if dry_run else "Importing"
                console.print(
                    f"[bold cyan]{action}[/bold cyan] "
                    f"[bold]{status.escape_for_console(environment)}[/bold] "
                    "environment into "
                    f"[bold]{status.escape_for_console(manifest_path.name)}[/bold]"
                )
                if imported.conda_dependencies:
                    console.print(
                        "Conda dependencies: "
                        + ", ".join(
                            status.escape_for_console(name)
                            for name in imported.conda_dependencies
                        )
                    )
                if imported.pypi_dependencies:
                    console.print(
                        "PyPI dependencies: "
                        + ", ".join(
                            status.escape_for_console(name)
                            for name in imported.pypi_dependencies
                        )
                    )
                if no_lockfile_update:
                    if not dry_run:
                        if LockfileInstallPlan.prefix_identity(prefix) is not None:
                            raise CondaWorkspacesError(
                                "Workspace environment prefix appeared while the "
                                f"import was being prepared: {prefix}"
                            )
                        publication.publish_manifest()
                else:
                    console.print()
                    sync_environments(
                        config,
                        ctx,
                        [environment],
                        no_install=no_install,
                        force_reinstall=force_reinstall,
                        dry_run=dry_run,
                        publish_lockfile=publication.publish_lockfile,
                        validate_workspace=publication.validate_manifest_generation,
                        require_absent_prefixes=(
                            [environment] if not force_reinstall else []
                        ),
                        console=console,
                    )
        except Exception as exc:
            if not publication.started:
                raise
            raise CondaWorkspacesError(
                "The imported environment declaration was published, but the "
                f"import did not finish: {redact_url_text(str(exc))}",
                hints=[
                    f"Run '{recovery_command}' to "
                    + (
                        "refresh conda.lock from the published manifest."
                        if no_install
                        else (
                            "refresh conda.lock and reconcile the imported environment."
                        )
                    )
                ],
            ) from exc
        if not dry_run:
            console.print(
                "[bold cyan]Imported[/bold cyan] "
                f"[bold]{status.escape_for_console(environment)}[/bold] "
                "environment into "
                f"[bold]{status.escape_for_console(manifest_path.name)}[/bold]"
            )
        return 0

    if getattr(args, "manifest_file", None) is not None:
        raise CondaWorkspacesError(
            "The global --file option requires --environment when importing."
        )
    if any(
        getattr(args, option, False)
        for option in ("no_install", "no_lockfile_update", "force_reinstall")
    ):
        raise CondaWorkspacesError(
            "--no-install, --no-lockfile-update, and --force-reinstall require "
            "--environment."
        )

    output: Path = getattr(args, "output", None) or Path("conda.toml")
    if output.is_symlink():
        raise ManifestImportError(output, "output cannot be a symbolic link")
    output_generation = regular_file_generation(output)
    doc = importer.convert(source)
    text = tomlkit.dumps(doc)
    CondaTomlParser().validate_no_url_credentials(
        doc.unwrap(),
        output,
        content=text,
    )
    if not quiet:
        status.message(
            console,
            "Detected",
            "format",
            importer.label,
        )

    if dry_run:
        if console.is_terminal:
            console.print(Syntax(text, "toml", theme="ansi_dark"))
        else:
            print(text, end="", file=console.file)
        raise DryRunExit()

    if output.is_file():
        try:
            confirm_yn(f"Overwrite {output}?")
        except (CondaSystemExit, DryRunExit):
            return 0

    atomic_write_text(
        output,
        text,
        expected_generation=output_generation,
    )
    if not quiet:
        parent = str(output.parent)
        status.message(
            console,
            "Wrote",
            "workspace",
            output.name,
            detail=parent if parent != "." else None,
        )

    return 0
