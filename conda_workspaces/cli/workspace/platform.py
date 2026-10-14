"""Edit explicit platform lists on existing named features."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from conda.base.constants import KNOWN_SUBDIRS
from conda.utils import quote_for_shell
from rich.console import Console
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import Array, InlineTable, Table

from ...context import WorkspaceContext
from ...exceptions import CondaWorkspacesError, PlatformError
from ...lockfile import (
    MAX_LOCKFILE_BYTES,
    CondaLockLoader,
    check_lockfile_satisfiability,
    load_lockfile_data,
    lockfile_path,
    render_lockfile,
)
from ...manifests import detect_workspace_file, find_parser
from ...models import Feature, LockfileStatus
from ...paths import read_regular_file_bytes
from ...publication import WorkspacePublication
from ...resolver import resolve_all_environments
from .. import status
from . import workspace_manifest_path_from_args
from .dependencies import workspace_toml_source
from .sync import affected_environments

if TYPE_CHECKING:
    import argparse


def execute_platform(
    args: argparse.Namespace, *, console: Console | None = None
) -> int:
    """Update feature platforms and lock targets without touching installed prefixes."""
    if console is None:
        console = Console(highlight=False)
    dry_run = bool(args.dry_run)
    feature_name = args.feature
    if feature_name == Feature.DEFAULT_NAME:
        raise CondaWorkspacesError(
            "Platform commands require an existing named feature.",
            hints=["The default feature and workspace platforms are not edited here."],
        )
    if args.manifest_file is not None:
        WorkspacePublication.validate_manifest_path(Path(args.manifest_file))
    selected_path = workspace_manifest_path_from_args(args, for_mutation=True)
    manifest_path = selected_path or detect_workspace_file()
    WorkspacePublication.validate_manifest_path(manifest_path)
    parser = find_parser(manifest_path)
    original_text = parser.read_manifest_text(manifest_path)
    document = parser.parse_toml_text_with_redacted_errors(original_text, manifest_path)
    source, _ = workspace_toml_source(document, manifest_path, create=False)
    features = source.get("feature") if source is not None else None
    if (
        not isinstance(features, Table | InlineTable | OutOfOrderTableProxy)
        or feature_name not in features
    ):
        raise CondaWorkspacesError(
            f"Feature '{feature_name}' is not defined in the workspace."
        )
    selected = features[feature_name]
    if not isinstance(selected, Table | InlineTable | OutOfOrderTableProxy):
        raise CondaWorkspacesError(f"Feature '{feature_name}' must be a TOML table.")
    declared = selected.get("platforms")
    if declared is not None and (
        not isinstance(declared, Array)
        or any(not isinstance(platform, str) for platform in declared)
    ):
        raise CondaWorkspacesError(
            f"Platforms for feature '{feature_name}' must be a list of platform names."
        )
    current = parser.parse_data_with_redacted_errors(document.unwrap(), manifest_path)
    known = set(KNOWN_SUBDIRS).union(current.platforms, current.platform_subdirs)
    requested = list(dict.fromkeys(args.platforms))
    for platform in requested:
        if platform not in known:
            raise PlatformError(platform, sorted(known))
        if args.platform_action == "remove" and (
            declared is None or platform not in declared
        ):
            raise CondaWorkspacesError(
                f"Platform '{platform}' is not explicitly declared for"
                f" feature '{feature_name}'."
            )

    changed = requested
    if args.platform_action == "add":
        changed = [
            platform
            for platform in requested
            if declared is None or platform not in declared
        ]
        if not changed:
            console.print("The requested platforms are already declared.")
            return 0
        if declared is None:
            selected["platforms"] = changed
        else:
            declared.extend(changed)
    else:
        assert declared is not None
        for platform in requested:
            while platform in declared:
                declared.remove(platform)
        if not declared:
            del selected["platforms"]

    updated_text = tomlkit.dumps(document)
    parser.validate_no_url_credentials(
        document.unwrap(), manifest_path, content=updated_text
    )
    config = parser.parse_data_with_redacted_errors(document.unwrap(), manifest_path)
    ctx = WorkspaceContext(config)
    publication = WorkspacePublication(
        ctx, manifest_path, original_text, updated_text, "platform update"
    )
    with nullcontext() if dry_run else publication.guard():
        if args.no_lockfile_update:
            if not dry_run:
                publication.publish_manifest()
        else:
            baseline_path = lockfile_path(ctx)
            try:
                baseline_path.lstat()
            except FileNotFoundError:
                baseline = None
            else:
                stale_reason = None
                try:
                    content = (
                        read_regular_file_bytes(
                            baseline_path,
                            maximum_bytes=MAX_LOCKFILE_BYTES,
                            label="workspace lockfile",
                        )
                        if dry_run
                        else publication.read_lockfile_bytes()
                    )
                    baseline = load_lockfile_data(content)
                    loader = CondaLockLoader(baseline_path, data=baseline)
                    locked_platforms = {
                        name: set(loader.platforms_for(name))
                        for name in loader.available_environments
                    }
                    if set(locked_platforms) != set(current.environments):
                        stale_reason = "Locked environments do not match the manifest."
                    for name, resolved in resolve_all_environments(current).items():
                        if stale_reason:
                            break
                        declared_platforms = set(resolved.platforms or [ctx.platform])
                        if locked_platforms[name] != declared_platforms:
                            stale_reason = (
                                f"Locked platforms for environment '{name}'"
                                " do not match its declarations."
                            )
                            break
                        for platform in declared_platforms:
                            lock_status = check_lockfile_satisfiability(
                                current, baseline, platform, environment=name
                            )
                            if lock_status.status != LockfileStatus.UP_TO_DATE:
                                stale_reason = (
                                    lock_status.reason
                                    or "The lockfile needs refreshing."
                                )
                                break
                except ValueError as exc:
                    stale_reason = str(exc)
                if stale_reason:
                    command = ["conda", "workspace"]
                    if selected_path is not None:
                        command.extend(("--file", str(selected_path)))
                    command.append("lock")
                    raise CondaWorkspacesError(
                        "The lockfile is not current for the existing manifest.",
                        hints=[
                            stale_reason,
                            f"Run '{quote_for_shell(*command)}' before editing"
                            " feature platforms.",
                        ],
                    )
            rendered = render_lockfile(
                ctx,
                resolve_all_environments(config),
                config=config,
                baseline_data=baseline,
                preserve_existing=baseline is not None,
                dry_run=dry_run,
            )
            if not dry_run:
                publication.publish_lockfile(rendered)

    action = "Would add" if dry_run else "Added"
    if args.platform_action == "remove":
        action = "Would remove" if dry_run else "Removed"
    preposition = "to" if args.platform_action == "add" else "from"
    console.print(
        status.escape_for_console(
            f"{action} {', '.join(changed)} {preposition} feature '{feature_name}'"
            f" in {manifest_path.name}."
        ),
        highlight=False,
    )
    if args.platform_action == "remove" and not config.features[feature_name].platforms:
        console.print(
            "With no explicit platform list, environments inherit platform support"
            " from their other features or the workspace."
        )
    names = affected_environments(config, feature_name)
    if names:
        console.print(
            "Affected environments: " + status.escape_for_console(", ".join(names))
        )
    return 0
