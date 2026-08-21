"""``conda workspace sbom`` convenience wrapper for conda-sboms."""

from __future__ import annotations

import importlib
from inspect import signature
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.exceptions import CondaValueError

from .export import execute_export

if TYPE_CHECKING:
    import argparse

    from conda.models.environment import Environment
    from rich.console import Console


def execute_sbom(
    args: argparse.Namespace,
    *,
    console: Console | None = None,
) -> int:
    """Export one workspace environment as a CycloneDX 1.7 SBOM."""
    platform = args.platform or conda_context.subdir

    args.format = "cyclonedx-json-v1.7"
    args.export_platforms = [platform]
    args.from_lockfile = not args.from_prefix
    args.from_history = args.from_prefix
    args.no_builds = False
    args.ignore_channels = False

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
    if not metadata_requested and not args.reproducible:
        return execute_export(
            args,
            console=console,
            include_requested_packages=True,
            host_prefix_only=True,
        )

    requirement = (
        "Reproducible output requires conda-sboms >=0.3.0. Install or upgrade "
        "conda-sboms in the environment that owns conda."
        if args.reproducible
        else "Per-export metadata requires conda-sboms >=0.2.0. Install or upgrade "
        "conda-sboms in the environment that owns conda."
    )
    try:
        CycloneDXExporter = importlib.import_module(
            "conda_sboms.cyclonedx"
        ).CycloneDXExporter
    except (AttributeError, ImportError) as exc:
        raise CondaValueError(requirement) from exc

    if args.reproducible:
        try:
            supports_reproducible = (
                "output_reproducible" in signature(CycloneDXExporter).parameters
            )
        except (TypeError, ValueError):
            supports_reproducible = False
        if not supports_reproducible:
            raise CondaValueError(requirement)

    metadata = None
    if metadata_requested:
        try:
            CycloneDXExportMetadata = importlib.import_module(
                "conda_sboms.settings"
            ).CycloneDXExportMetadata
        except (AttributeError, ImportError) as exc:
            raise CondaValueError(
                "Per-export metadata requires conda-sboms >=0.2.0. Install or "
                "upgrade conda-sboms in the environment that owns conda."
            ) from exc
        metadata = CycloneDXExportMetadata(**metadata_values)

    def render(environment: Environment) -> str:
        if args.reproducible:
            return CycloneDXExporter(
                environment,
                metadata=metadata,
                output_reproducible=True,
            ).export()
        return CycloneDXExporter(environment, metadata=metadata).export()

    return execute_export(
        args,
        console=console,
        export_environment=render,
        include_requested_packages=True,
        host_prefix_only=True,
    )
