"""``conda workspace sbom`` convenience wrapper for conda-sboms."""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from importlib import import_module
from inspect import signature
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.exceptions import CondaValueError
from rich.console import Console

from .export import execute_export

if TYPE_CHECKING:
    from conda.models.environment import Environment


def execute_sbom(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Export one workspace environment as a CycloneDX 1.7 SBOM."""
    if console is None:
        console = Console(highlight=False)

    export_args = argparse.Namespace(**vars(args))
    platform = args.platform or conda_context.subdir

    export_args.export_platforms = [platform]
    export_args.from_lockfile = not args.from_prefix
    export_args.from_history = args.from_prefix
    export_args.no_builds = False
    export_args.ignore_channels = False
    export_args.json = False

    metadata_values = {
        "product_name": args.product_name,
        "product_version": args.product_version,
        "product_manufacturer": args.product_manufacturer,
        "product_manufacturer_url": args.product_manufacturer_url,
        "author_name": args.author_name,
        "author_email": args.author_email,
        "author_organization": args.author_organization,
        "author_organization_url": args.author_organization_url,
    }
    metadata_requested = any(value is not None for value in metadata_values.values())
    install_hint = (
        "Install or upgrade conda-sboms in the environment where conda is installed."
    )
    metadata_requirement = (
        f"Per-export metadata requires conda-sboms >=0.2.0. {install_hint}"
    )
    reproducible_requirement = (
        f"Reproducible output requires conda-sboms >=0.3.0. {install_hint}"
    )
    base_requirement = f"SBOM export requires conda-sboms >=0.1.1. {install_hint}"
    try:
        cyclonedx = import_module("conda_sboms.cyclonedx")
        format_name = str(cyclonedx.FORMAT)
        export_cyclonedx_json = cyclonedx.export_cyclonedx_json
    except (AttributeError, ImportError) as exc:
        requirement = base_requirement
        if metadata_requested:
            requirement = metadata_requirement
        if args.reproducible:
            requirement = reproducible_requirement
        raise CondaValueError(requirement) from exc

    export_args.format = format_name

    exporter_parameters = signature(export_cyclonedx_json).parameters
    if metadata_requested and "metadata" not in exporter_parameters:
        raise CondaValueError(metadata_requirement)
    if args.reproducible and "output_reproducible" not in exporter_parameters:
        raise CondaValueError(reproducible_requirement)

    metadata = None
    if metadata_requested:
        try:
            CycloneDXExportMetadata = import_module(
                "conda_sboms.settings"
            ).CycloneDXExportMetadata
        except (AttributeError, ImportError) as exc:
            raise CondaValueError(metadata_requirement) from exc
        metadata = CycloneDXExportMetadata(**metadata_values)

    def render(environment: Environment) -> str:
        export_options: dict[str, object] = {}
        if metadata_requested:
            export_options["metadata"] = metadata
        if args.reproducible:
            export_options["output_reproducible"] = True
        return export_cyclonedx_json(environment, **export_options)

    if not args.json:
        return execute_export(
            export_args,
            console=console,
            export_environment=render,
            include_requested_packages=True,
            host_prefix_only=True,
        )

    captured_stdout = io.StringIO()
    nested_console = Console(
        file=io.StringIO(),
        highlight=False,
        force_terminal=False,
        no_color=True,
    )
    with redirect_stdout(captured_stdout):
        execute_export(
            export_args,
            console=nested_console,
            export_environment=render,
            include_requested_packages=True,
            host_prefix_only=True,
        )

    payload: dict[str, object] = {
        "success": True,
        "format": format_name,
        "environment": args.environment or "default",
    }
    if args.output is not None and not args.dry_run:
        payload["file"] = str(args.output)
    else:
        payload["content"] = captured_stdout.getvalue()
    console.print_json(json.dumps(payload))
    return 0
