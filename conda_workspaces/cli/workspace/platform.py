"""Edit workspace and named-feature platform lists."""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING

import tomlkit
from conda.base.constants import KNOWN_SUBDIRS
from conda.utils import quote_for_shell
from rich.console import Console
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import AoT, Array, InlineTable, Table

from ...context import WorkspaceContext
from ...exceptions import CondaWorkspacesError, PlatformError
from ...lockfile import (
    MAX_LOCKFILE_BYTES,
    CondaLockLoader,
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
    """Update platform declarations and locks without touching installed prefixes."""
    if console is None:
        console = Console(highlight=False)
    dry_run = bool(args.dry_run)
    feature_name = args.feature
    if feature_name == Feature.DEFAULT_NAME:
        raise CondaWorkspacesError(
            "The default feature cannot have a separate platform list.",
            hints=["Omit --feature to edit the workspace platforms."],
        )
    manifest_path = args.manifest_file or detect_workspace_file()
    WorkspacePublication.validate_manifest_path(manifest_path)
    selected_path = workspace_manifest_path_from_args(args, for_mutation=True)
    manifest_path = selected_path or manifest_path
    parser = find_parser(manifest_path)
    original_text = parser.read_manifest_text(manifest_path)
    document = parser.parse_toml_text_with_redacted_errors(original_text, manifest_path)
    source, _ = workspace_toml_source(document, manifest_path, create=False)
    target = "the workspace" if feature_name is None else f"feature '{feature_name}'"
    if feature_name is None:
        selected = (
            source.get("workspace") or source.get("project")
            if source is not None
            else None
        )
    else:
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
        raise CondaWorkspacesError(f"Platforms for {target} require a TOML table.")
    declared = selected.get("platforms")
    if declared is not None and (
        not isinstance(declared, Array if feature_name is not None else Array | AoT)
        or (
            feature_name is not None
            and any(not isinstance(platform, str) for platform in declared)
        )
    ):
        raise CondaWorkspacesError(
            f"Platforms for {target} must be a list of platform names"
            + (" or target tables." if feature_name is None else ".")
        )
    current = parser.parse_data_with_redacted_errors(document.unwrap(), manifest_path)
    declared_names = (
        current.platforms
        if feature_name is None
        else current.features[feature_name].platforms
    )
    known = set(KNOWN_SUBDIRS).union(current.platforms) - {"noarch"}
    requested = list(dict.fromkeys(args.platforms))
    for platform in requested:
        if args.platform_action == "add":
            if current.platform_subdir(platform) == "noarch":
                raise CondaWorkspacesError(
                    "'noarch' is a package subdirectory, not a target platform."
                )
            if platform not in known:
                raise PlatformError(platform, sorted(known))
        elif platform not in declared_names:
            raise CondaWorkspacesError(
                f"Platform '{platform}' is not explicitly declared for {target}."
            )
        elif feature_name is None and (
            current.platform_subdir(platform) != platform
            or current.platform_system_requirements.get(platform)
        ):
            references = [
                name
                for name, feature in current.features.items()
                if platform in feature.platforms
            ]
            if references:
                raise CondaWorkspacesError(
                    f"Platform '{platform}' is still used by features: "
                    + ", ".join(references),
                    hints=[
                        "Remove those feature references before removing the workspace"
                        " target and its platform settings."
                    ],
                )

    changed = requested
    if args.platform_action == "add":
        changed = [platform for platform in requested if platform not in declared_names]
        if not changed:
            console.print("The requested platforms are already declared.")
            return 0
        if declared is None:
            selected["platforms"] = changed
        elif isinstance(declared, AoT):
            for platform in changed:
                declared.append({"platform": platform})
        else:
            declared.extend(changed)
    else:
        assert declared is not None
        for index in reversed(range(len(declared_names))):
            if declared_names[index] in requested:
                del declared[index]
        if not declared:
            if feature_name is not None:
                del selected["platforms"]
            elif isinstance(declared, AoT):
                selected["platforms"] = []

    updated_text = tomlkit.dumps(document)
    parser.validate_no_url_credentials(
        document.unwrap(), manifest_path, content=updated_text
    )
    config = parser.parse_data_with_redacted_errors(document.unwrap(), manifest_path)
    ctx = WorkspaceContext(config)
    current_envs = resolve_all_environments(current) if feature_name is None else {}
    updated_envs = (
        resolve_all_environments(config)
        if feature_name is None or not args.no_lockfile_update
        else {}
    )
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
                    if feature_name is not None:
                        current_envs = resolve_all_environments(current)
                    for name, resolved in current_envs.items():
                        if stale_reason:
                            break
                        declared_platforms = set(resolved.platforms or [ctx.platform])
                        if locked_platforms[name] != declared_platforms:
                            stale_reason = (
                                f"Locked platforms for environment '{name}'"
                                " do not match its declarations."
                            )
                            break
                    if stale_reason is None:
                        targets = (
                            (name, platform)
                            for name, resolved in current_envs.items()
                            for platform in (resolved.platforms or [ctx.platform])
                        )
                        lock_status = loader.check_targets(
                            current, targets, match_budget_scope="target"
                        )
                        if lock_status.status != LockfileStatus.UP_TO_DATE:
                            stale_reason = (
                                lock_status.reason or "The lockfile needs refreshing."
                            )
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
                            " platforms.",
                        ],
                    )
            rendered = render_lockfile(
                ctx,
                updated_envs,
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
            f"{action} {', '.join(changed)} {preposition} {target}"
            f" in {manifest_path.name}."
        ),
        highlight=False,
    )
    if args.platform_action == "remove":
        if feature_name is None and not config.platforms:
            console.print(
                "With no workspace platform list, environments without explicit feature"
                " platforms use the native platform."
            )
        elif feature_name is not None and not config.features[feature_name].platforms:
            console.print(
                "With no explicit platform list, environments inherit platform support"
                " from their other features or the workspace."
            )
    if feature_name is None:
        names = [
            name
            for name, environment in updated_envs.items()
            if environment.platforms != current_envs[name].platforms
        ]
    else:
        names = affected_environments(config, feature_name)
    if names:
        console.print(
            "Affected environments: " + status.escape_for_console(", ".join(names))
        )
    return 0
