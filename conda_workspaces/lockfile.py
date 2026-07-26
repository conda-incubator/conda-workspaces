"""Lockfile generation, consumption and env-spec plugin for ``conda.lock``.

Single source of truth for every ``conda.lock`` concern.  The file owns
the read path, the write path, the ``CondaEnvironmentSpecifier`` plugin
class (:class:`CondaLockLoader`), and the plugin metadata (name,
aliases, default filename) consumed by ``plugin.py`` and
:mod:`.export`.

The ``conda.lock`` format is a *derivative* of rattler-lock v6
(``pixi.lock``): same schema machinery, same top-level keys
(``version``, ``environments``, ``packages``), but with an on-disk
``version: 1`` byte that identifies the file as conda-workspaces-owned.
:class:`CondaLockLoader` shares rattler-lock v6 conversion models with
:mod:`conda_lockfiles.rattler_lock.v6`. The read path performs an in-memory
``version: 6`` swap before delegating YAML -> ``Environment`` conversion; the
write path uses the same public package model while preserving conda-workspaces'
multi-environment and external-package layout.

The file layout is::

    version: 1
    environments:
      <name>:
        channels: [{url: ...}, ...]
        packages:
          <platform>: [{conda: <url>}, ...]
    packages:
      - conda: <url>
        sha256: ...
        md5: ...
        depends: [...]
        ...

On the *write* side, :func:`generate_lockfile` solves each environment
and delegates YAML serialisation to the ``multiplatform_export`` hook
in :mod:`.export` (the same path ``conda export`` uses), so every
``conda.lock`` on disk comes out of a single formatter.  On the *read*
side, :func:`install_from_lockfile` extracts the package list for one
environment + platform via :class:`CondaLockLoader` and installs the
exact URLs, bypassing the solver entirely.
"""

from __future__ import annotations

import io
import sys
import tempfile
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from conda.models.dist import Dist
from conda.plugins.types import EnvironmentSpecBase

from .context import isolated_package_cache
from .exceptions import (
    AllTargetsUnsolvableError,
    EnvironmentNotFoundError,
    LockfileIntegrityError,
    LockfileMergeError,
    LockfileNotFoundError,
    LockfileStaleError,
    PlatformError,
    SolveError,
)
from .models import LockfileStatus
from .paths import (
    atomic_write_text,
    output_paths_collide,
    validate_directory_output,
    validate_file_output,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from typing import Any, ClassVar, Final

    from conda.common.path import PathType
    from conda.models.environment import Environment, EnvironmentConfig
    from conda.models.match_spec import MatchSpec
    from conda.models.records import PackageRecord

    from .context import WorkspaceContext
    from .models import Environment as WorkspaceEnvironment
    from .models import WorkspaceConfig
    from .resolver import ResolvedEnvironment

#: On-disk lockfile format version.  Distinct from the rattler-lock v6
#: schema version so that tools can tell a conda-workspaces-owned lock
#: from a pixi-owned one at a glance.
LOCKFILE_VERSION: Final = 1

#: The canonical lockfile filename.
LOCKFILE_NAME: Final = "conda.lock"

#: Canonical, versioned plugin name.  Stable across schema bumps;
#: follows the ``conda-lockfiles`` alias policy (see
#: ``docs/reference/format-aliases.md``).
FORMAT: Final = "conda-workspaces-lock-v1"

#: User-friendly aliases.  Unversioned names are convenience handles
#: that may migrate to a newer ``FORMAT`` in the future.
ALIASES: Final = ("conda-workspaces-lock", "workspace-lock")

#: Default filenames this plugin handles.
DEFAULT_FILENAMES: Final = (LOCKFILE_NAME,)


@dataclass
class _SolvedEnvironment:
    """Solved lockfile row that can carry a Pixi rich-platform name."""

    name: str
    platform: str
    package_platform: str
    config: EnvironmentConfig
    explicit_packages: Sequence[PackageRecord]
    external_packages: dict[str, list[str]] = field(default_factory=dict)


def load_lockfile_data(content: str | bytes) -> Any:
    """Parse in-memory lockfile YAML with the same safe loader as disk reads."""
    from conda.common.serialize import yaml_safe_load

    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return yaml_safe_load(content)


def lockfile_path(ctx: WorkspaceContext) -> Path:
    """Return the path to the workspace lockfile (``<root>/conda.lock``)."""
    return ctx.root / LOCKFILE_NAME


def _normalize_channel_urls(urls: Iterable[str]) -> list[str]:
    """Strip trailing slashes from channel URLs for comparison."""
    return [url.rstrip("/") for url in urls]


def _manifest_channel_entries(
    config: WorkspaceConfig,
    environment: WorkspaceEnvironment,
) -> list[dict[str, str]]:
    """Return manifest channel entries without conda canonical-name deduplication."""
    seen: set[str] = set()
    entries: list[dict[str, str]] = []
    for ch in config.channels:
        url = str(ch)
        normalized = url.rstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            entries.append({"url": url})
    for feature in config.resolve_features(environment):
        for ch in feature.channels:
            url = str(ch)
            normalized = url.rstrip("/")
            if normalized not in seen:
                seen.add(normalized)
                entries.append({"url": url})
    return entries


def lockfile_status(
    ctx: WorkspaceContext,
    config: WorkspaceConfig,
) -> LockfileStatus:
    """Determine the lockfile status relative to the workspace manifest."""
    lock = lockfile_path(ctx)
    if not lock.is_file():
        return LockfileStatus(status=LockfileStatus.MISSING)

    from conda_lockfiles.load_yaml import load_yaml

    data = load_yaml(lock)
    return check_lockfile_satisfiability(config, data, ctx.platform)


def check_lockfile_satisfiability(
    config: WorkspaceConfig,
    lockfile_data: dict[str, Any],
    current_platform: str,
) -> LockfileStatus:
    """Check whether *lockfile_data* satisfies the manifest's requirements.

    Returns a :class:`LockfileStatus` with ``status=UP_TO_DATE`` when
    the lockfile covers every environment, platform, channel, and
    dependency declared in *config*.  Returns ``status=OUT_OF_DATE``
    with a human-readable *reason* otherwise.
    """
    _stale = LockfileStatus.OUT_OF_DATE

    if lockfile_data.get("version") != LOCKFILE_VERSION:
        return LockfileStatus(
            status=_stale,
            reason=(
                f"Lockfile version {lockfile_data.get('version')!r} "
                f"does not match expected version {LOCKFILE_VERSION}"
            ),
        )

    from .resolver import resolve_environment

    lock_envs = lockfile_data.get("environments", {})
    for env_name, env_obj in config.environments.items():
        if env_name not in lock_envs:
            return LockfileStatus(
                status=_stale,
                reason=(
                    f"Environment '{env_name}' is declared in the manifest "
                    f"but missing from the lockfile"
                ),
            )

        lock_env = lock_envs[env_name]

        manifest_channels = _manifest_channel_entries(config, env_obj)
        manifest_urls = _normalize_channel_urls(
            entry["url"] for entry in manifest_channels
        )
        lock_channel_entries = lock_env.get("channels", [])
        lock_urls = _normalize_channel_urls(
            entry.get("url", "") for entry in lock_channel_entries
        )
        if manifest_urls != lock_urls:
            return LockfileStatus(
                status=_stale,
                reason=(
                    f"Channel mismatch for environment '{env_name}': "
                    f"manifest declares {manifest_urls} "
                    f"but lockfile has {lock_urls}"
                ),
            )

        lock_platforms = set(lock_env.get("packages", {}))
        resolved_platforms = resolve_environment(config, env_name).platforms
        manifest_platforms = resolved_platforms or config.platforms
        for platform in manifest_platforms:
            if platform not in lock_platforms:
                return LockfileStatus(
                    status=_stale,
                    reason=(
                        f"Platform '{platform}' is declared in the "
                        f"manifest but missing from environment "
                        f"'{env_name}' in the lockfile"
                    ),
                )

        lock_packages = lock_env.get("packages", {})
        try:
            current_lock_platform = config.resolve_platform_name(
                current_platform,
                manifest_platforms,
            )
        except PlatformError:
            current_lock_platform = current_platform
        platform_refs = lock_packages.get(current_lock_platform)
        if platform_refs is None:
            continue
        package_platform = config.platform_subdir(current_lock_platform)

        try:
            channel_urls = CondaLockLoader.channel_urls_for_env_data(
                lock_env,
                package_platform,
            )
            records = CondaLockLoader.package_records_for_env_data(
                lockfile_data,
                env_name,
                current_lock_platform,
                package_platform=package_platform,
            )
        except ValueError as exc:
            return LockfileStatus(status=_stale, reason=str(exc))

        records_by_name = {}
        for record in records:
            url = record.url
            if not isinstance(url, str) or not url:
                return LockfileStatus(
                    status=_stale,
                    reason=(
                        f"Environment '{env_name}' contains an invalid "
                        f"package ref on '{current_lock_platform}'"
                    ),
                )
            if not CondaLockLoader.url_matches_channel(url, channel_urls):
                return LockfileStatus(
                    status=_stale,
                    reason=(
                        f"Package URL '{url}' in environment '{env_name}' "
                        f"on '{current_lock_platform}' is not under a declared "
                        "channel"
                    ),
                )
            if record.name in records_by_name:
                return LockfileStatus(
                    status=_stale,
                    reason=(
                        f"Environment '{env_name}' contains more than one "
                        f"'{record.name}' package on '{current_lock_platform}'"
                    ),
                )
            records_by_name[record.name] = record

        from conda.base.context import context as conda_context
        from conda.models.match_spec import MatchSpec

        from .envs import _apply_system_requirements, _build_pypi_specs

        target_resolved = resolve_environment(
            config,
            env_name,
            current_lock_platform,
        )
        requested_specs = [
            *target_resolved.conda_dependencies.values(),
            *_build_pypi_specs(target_resolved),
        ]
        _apply_system_requirements(target_resolved, requested_specs)
        dependencies = [
            (record, MatchSpec(dependency))
            for record in records
            for dependency in record.depends
        ]
        constraints = [
            (record, MatchSpec(constraint))
            for record in records
            for constraint in record.constrains
        ]
        virtual_names = {
            spec.name
            for spec in (
                *requested_specs,
                *(spec for _, spec in dependencies),
                *(spec for _, spec in constraints),
            )
            if spec.name and spec.name.startswith("__")
        }
        candidates = list(records)
        if virtual_names:
            with (
                target_resolved.scoped_virtual_packages(package_platform),
                conda_context._override("_subdir", package_platform),
            ):
                candidates.extend(
                    conda_context.plugin_manager.get_virtual_package_records()
                )
        candidates_by_name = {record.name: record for record in candidates}

        for spec in requested_specs:
            dep_name = spec.name
            record = candidates_by_name.get(dep_name)
            if record is None:
                return LockfileStatus(
                    status=_stale,
                    reason=(
                        f"Dependency '{dep_name}' is required by "
                        f"environment '{env_name}' but not found in "
                        f"the lockfile for platform "
                        f"'{current_lock_platform}'"
                    ),
                )
            if not spec.match(record):
                return LockfileStatus(
                    status=_stale,
                    reason=(
                        f"Dependency '{dep_name}' in environment "
                        f"'{env_name}' requires '{spec}' but locked package "
                        f"'{record.dist_str()}' on '{current_lock_platform}' "
                        "does not satisfy it"
                    ),
                )

        for record, spec in dependencies:
            dep_name = spec.get_exact_value("name")
            if dep_name is None:
                satisfied = any(spec.match(candidate) for candidate in candidates)
            else:
                candidate = candidates_by_name.get(dep_name)
                satisfied = candidate is not None and spec.match(candidate)
            if not satisfied:
                return LockfileStatus(
                    status=_stale,
                    reason=(
                        f"Package '{record.dist_str()}' in environment "
                        f"'{env_name}' requires '{spec}', which is missing "
                        f"on '{current_lock_platform}'"
                    ),
                )

        for record, spec in constraints:
            candidate = candidates_by_name.get(spec.name)
            if candidate is not None and not spec.match(candidate):
                return LockfileStatus(
                    status=_stale,
                    reason=(
                        f"Package '{record.dist_str()}' in environment "
                        f"'{env_name}' constrains '{spec}', but "
                        f"'{candidate.dist_str()}' does not satisfy it on "
                        f"'{current_lock_platform}'"
                    ),
                )

    return LockfileStatus(status=LockfileStatus.UP_TO_DATE)


class CondaLockLoader(EnvironmentSpecBase):
    """Environment specifier + loader for ``conda.lock``.

    ``conda.lock`` is a derivative of rattler-lock v6 (``pixi.lock``);
    this loader shares the rattler-lock v6 conversion helper from
    :mod:`conda_lockfiles.rattler_lock.v6` by performing an in-memory
    ``version: 1 -> 6`` swap before handing the data off.  The on-disk
    file keeps ``version: 1`` unchanged.

    Used by ``conda env create --file conda.lock`` (single platform via
    ``env``) and by ``conda workspace install`` (multi-platform via
    ``env_for``).
    """

    detection_supported: ClassVar[bool] = True

    def __init__(self, path: PathType) -> None:
        self.path = Path(path).resolve()
        self._data_cache: dict[str, Any] | None = None

    def can_handle(self) -> bool:
        if self.path.name not in DEFAULT_FILENAMES:
            return False
        if not self.path.exists():
            return False
        try:
            return self._data.get("version") == LOCKFILE_VERSION
        except Exception:
            return False

    @property
    def _data(self) -> dict[str, Any]:
        if self._data_cache is None:
            from conda_lockfiles.load_yaml import load_yaml

            self._data_cache = load_yaml(self.path)
        return self._data_cache

    @property
    def available_platforms(self) -> tuple[str, ...]:
        """Platforms declared in this lockfile's default environment."""
        env_data = self._env_data("default")
        return tuple(sorted(env_data.get("packages", {})))

    def env_for(self, platform: str, name: str = "default") -> Environment:
        """Return the conda ``Environment`` for *platform* and *name*.

        Raises ``PlatformMismatchError`` if *platform* is not in the
        lockfile or *name* does not identify a declared environment.
        """
        env_data = self._env_data(name)
        platforms = tuple(sorted(env_data.get("packages", {})))
        if platform not in platforms:
            from conda.exceptions import PlatformMismatchError

            raise PlatformMismatchError(
                incompatible=[(str(self.path), platforms)],
                subdir=platform,
            )

        # Share rattler-lock v6 conversion with conda-lockfiles via a
        # localised in-memory version byte swap.  Disk file is untouched.
        from conda_lockfiles.rattler_lock.v6 import (
            RattlerLockV6,
            rattler_lock_v6_to_conda_env,
        )

        payload = dict(self._data)
        payload["version"] = 6
        lockfile_model = RattlerLockV6.model_validate(payload)
        env = rattler_lock_v6_to_conda_env(lockfile_model, name=name, platform=platform)
        env.name = name
        return env

    @property
    def env(self) -> Environment:
        """Return the default environment for the current subdir.

        Kept for backwards compatibility with ``conda env create --file
        conda.lock``; delegates to :meth:`env_for`.
        """
        from conda.base.context import context

        return self.env_for(context.subdir)

    def _env_data(self, name: str = "default") -> dict[str, Any]:
        data = self._data
        if data.get("version") != LOCKFILE_VERSION:
            raise ValueError(
                f"Unsupported {LOCKFILE_NAME} version: {data.get('version')!r} "
                f"(expected {LOCKFILE_VERSION})"
            )
        environments = data.get("environments", {})
        if name not in environments:
            from conda.common.io import dashlist

            raise ValueError(
                f"Environment {name!r} not found in lockfile. "
                f"Available environments: {dashlist(sorted(environments))}"
            )
        return environments[name]

    def explicit_package_specs_for(
        self,
        platform: str,
        name: str = "default",
        *,
        package_platform: str | None = None,
    ) -> list[str]:
        """Return hash-bearing explicit conda specs for *name* on *platform*."""
        env_data = self._env_data(name)
        platform_refs = env_data.get("packages", {}).get(platform)
        if platform_refs is None:
            raise ValueError(
                f"Environment {name!r} does not include packages for {platform!r}"
            )

        records_by_url = self.package_records_by_url()
        channel_urls = self.channel_urls_for(
            env_data,
            package_platform or platform,
        )
        explicit_specs: list[str] = []
        for ref in platform_refs:
            if not isinstance(ref, dict) or "conda" not in ref:
                continue
            url = ref["conda"]
            if not isinstance(url, str) or not url:
                raise LockfileIntegrityError(
                    self.path,
                    f"environment {name!r} has an invalid conda package ref",
                )
            if not self.url_matches_channel(url, channel_urls):
                raise LockfileIntegrityError(
                    self.path,
                    f"package URL {url!r} is not under any channel declared "
                    f"for environment {name!r}",
                )
            record = records_by_url.get(url)
            if record is None:
                raise LockfileIntegrityError(
                    self.path,
                    f"package URL {url!r} has no top-level package record",
                )
            digest = self.digest_fragment_for(record, url)
            explicit_specs.append(f"{url}#{digest}")
        return explicit_specs

    def package_records_by_url(self) -> dict[str, dict[str, Any]]:
        """Return top-level conda package records keyed by their exact URL."""
        return self.package_records_by_url_from_data(self._data)

    @staticmethod
    def package_records_by_url_from_data(
        data: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Return top-level conda package records from *data* keyed by URL."""
        records_by_url: dict[str, dict[str, Any]] = {}
        for record in data.get("packages", []) or []:
            if not isinstance(record, dict):
                continue
            url = record.get("conda") or record.get("url")
            if isinstance(url, str) and url:
                records_by_url.setdefault(url, record)
        return records_by_url

    @classmethod
    def package_records_for_env_data(
        cls,
        data: dict[str, Any],
        name: str,
        platform: str,
        *,
        package_platform: str | None = None,
    ) -> list[PackageRecord]:
        """Reconstruct package records for one lockfile environment slice.

        This path uses the metadata already embedded in ``conda.lock``. It
        deliberately avoids :meth:`env_for`, whose generic rattler-lock
        conversion may fetch package archives to fill missing metadata.
        """
        from conda.models.records import PackageRecord

        environments = data.get("environments", {})
        env_data = environments.get(name)
        if not isinstance(env_data, dict):
            raise ValueError(f"Environment {name!r} is missing from the lockfile")
        refs = env_data.get("packages", {}).get(platform)
        if refs is None:
            raise ValueError(
                f"Environment {name!r} does not include platform {platform!r}"
            )

        records_by_url = cls.package_records_by_url_from_data(data)
        records: list[PackageRecord] = []
        for ref in refs:
            if not isinstance(ref, dict) or "conda" not in ref:
                continue
            url = ref["conda"]
            if not isinstance(url, str) or not url:
                raise ValueError(
                    f"Environment {name!r} has an invalid package reference"
                )
            metadata = records_by_url.get(url)
            if metadata is None:
                raise ValueError(f"Package URL {url!r} has no top-level record")
            cls.digest_fragment_for_record(metadata, url)
            dist = Dist(url)
            records.append(
                PackageRecord.from_objects(
                    metadata,
                    name=dist.name,
                    version=dist.version,
                    build=dist.build_string,
                    build_number=dist.build_number,
                    channel=dist.channel,
                    subdir=dist.subdir or package_platform or platform,
                    fn=dist.to_filename(),
                    url=url,
                )
            )
        return records

    @classmethod
    def seed_prefix_from_data(
        cls,
        data: dict[str, Any],
        name: str,
        platform: str,
        prefix: Path,
        requested_specs: Iterable[MatchSpec],
        *,
        package_platform: str | None = None,
    ) -> list[PackageRecord]:
        """Create a metadata-only prefix from one canonical lockfile slice."""
        from conda.core.prefix_data import PrefixData
        from conda.history import History
        from conda.models.records import PrefixRecord

        records = cls.package_records_for_env_data(
            data,
            name,
            platform,
            package_platform=package_platform,
        )
        (prefix / "conda-meta").mkdir(parents=True)
        prefix_data = PrefixData(str(prefix))
        for record in records:
            prefix_data.insert(PrefixRecord.from_objects(record))
        history = History(str(prefix))
        history.write_changes(set(), {record.dist_str() for record in records})
        history.write_specs(update_specs=tuple(requested_specs))
        return records

    @classmethod
    def replace_solutions(
        cls,
        baseline: dict[str, Any],
        envs: Iterable[_SolvedEnvironment],
    ) -> dict[str, Any]:
        """Replace solved environment slices and canonicalize package records."""
        result = deepcopy(baseline)
        updates = cls.compose(envs)
        for name, update in updates["environments"].items():
            if name not in result.get("environments", {}):
                raise ValueError(f"Environment {name!r} is missing from the lockfile")
            result_env = result["environments"][name]
            result_env["channels"] = update["channels"]
            for platform, refs in update["packages"].items():
                if platform not in result_env.get("packages", {}):
                    raise ValueError(
                        f"Environment {name!r} does not include platform {platform!r}"
                    )
                result_env["packages"][platform] = refs

        packages_by_url = {
            record.get("conda") or record.get("url") or record.get("pypi"): record
            for record in (
                *(baseline.get("packages") or []),
                *(updates.get("packages") or []),
            )
            if record.get("conda") or record.get("url") or record.get("pypi")
        }
        packages: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for environment in result.get("environments", {}).values():
            for platform in sorted(environment.get("packages", {})):
                for ref in environment["packages"][platform]:
                    url = ref.get("conda") or ref.get("url") or ref.get("pypi")
                    if not url or url in seen_urls:
                        continue
                    record = packages_by_url.get(url)
                    if record is not None:
                        packages.append(record)
                        seen_urls.add(url)
        result["packages"] = packages
        return result

    def channel_urls_for(
        self,
        env_data: dict[str, Any],
        platform: str,
    ) -> tuple[str, ...]:
        """Return concrete package-containing channel URLs for *platform*."""
        return self.channel_urls_for_env_data(env_data, platform, path=self.path)

    @staticmethod
    def channel_urls_for_env_data(
        env_data: dict[str, Any],
        platform: str,
        *,
        path: Path | None = None,
    ) -> tuple[str, ...]:
        """Return concrete package-containing channel URLs for *platform*."""
        from conda.models.channel import Channel

        urls: set[str] = set()
        for entry in env_data.get("channels", []) or []:
            if not isinstance(entry, dict):
                continue
            raw_url = entry.get("url")
            if not isinstance(raw_url, str) or not raw_url:
                continue
            try:
                channel = Channel(raw_url)
                for with_credentials in (False, True):
                    urls.update(
                        channel.urls(
                            with_credentials=with_credentials,
                            subdirs=(platform, "noarch"),
                        )
                    )
            except Exception as exc:
                reason = f"environment channel {raw_url!r} cannot be resolved"
                if path is None:
                    raise ValueError(reason) from exc
                raise LockfileIntegrityError(path, reason) from exc
        return tuple(sorted(url.rstrip("/") for url in urls))

    @staticmethod
    def url_matches_channel(url: str, channel_urls: tuple[str, ...]) -> bool:
        """Return whether *url* is contained by one declared channel URL."""
        return any(url.startswith(f"{channel_url}/") for channel_url in channel_urls)

    def digest_fragment_for(self, record: dict[str, Any], url: str) -> str:
        """Return the conda explicit-file digest fragment for *record*."""
        return self.digest_fragment_for_record(record, url, path=self.path)

    @classmethod
    def digest_fragment_for_record(
        cls,
        record: dict[str, Any],
        url: str,
        *,
        path: Path | None = None,
    ) -> str:
        """Return the conda explicit-file digest fragment for *record*."""
        sha256 = record.get("sha256")
        if sha256 is not None:
            if cls.is_hex_digest(sha256, 64):
                return f"sha256:{sha256.lower()}"
            reason = f"package URL {url!r} has an invalid sha256 digest"
            if path is None:
                raise ValueError(reason)
            raise LockfileIntegrityError(path, reason)

        md5 = record.get("md5")
        if md5 is not None:
            if cls.is_hex_digest(md5, 32):
                return md5.lower()
            reason = f"package URL {url!r} has an invalid md5 digest"
            if path is None:
                raise ValueError(reason)
            raise LockfileIntegrityError(path, reason)

        reason = f"package URL {url!r} is missing a sha256 or md5 digest"
        if path is None:
            raise ValueError(reason)
        raise LockfileIntegrityError(path, reason)

    @staticmethod
    def is_hex_digest(value: object, length: int) -> bool:
        """Return whether *value* is a hex digest with *length* characters."""
        if not isinstance(value, str) or len(value) != length:
            return False
        try:
            int(value, 16)
        except ValueError:
            return False
        return True

    @classmethod
    def compose(
        cls,
        envs: Iterable[Environment | _SolvedEnvironment],
    ) -> dict[str, Any]:
        """Compose ``Environment`` objects into a ``conda.lock`` dict.

        The write-side companion to :meth:`env_for`: same loader class
        owns both directions.  Returned dict has the canonical
        ``version`` / ``environments`` / ``packages`` shape that
        :func:`.export.multiplatform_export` (our
        ``conda-workspaces-lock-v1`` plugin callable) then hands to
        conda's YAML dumper.  Exposed as a public classmethod because
        callers that want to inspect, merge, or hand off to a
        different serialiser can reuse the same composition logic
        without re-implementing it.
        """
        from conda.models.environment import Environment, EnvironmentConfig
        from conda_lockfiles.rattler_lock.v6 import RattlerLockV6Package
        from conda_lockfiles.validate_urls import validate_urls

        seen_urls: set[str] = set()
        packages: list[dict[str, Any]] = []
        environments: dict[str, dict[str, Any]] = {}

        for env in envs:
            # ruamel.yaml dispatches representers by exact type on dict
            # keys, so any ``str`` subclass reaching this point (e.g. a
            # leaked ``tomlkit.items.String``) raises ``TypeError:
            # Object of type ... is not YAML serializable``.  Workspace
            # parsers unwrap tomlkit docs at load time; this is the
            # last-line guard for callers that build ``Environment``
            # objects through other paths (``conda export`` plugin,
            # tests, third parties).
            env_name = str(env.name or "default")
            platform = str(env.platform)
            package_platform = str(getattr(env, "package_platform", platform))
            validation_env = env
            if package_platform != platform:
                validation_env = Environment(
                    name=env_name,
                    platform=package_platform,
                    config=EnvironmentConfig(channels=tuple(env.config.channels)),
                    explicit_packages=list(env.explicit_packages),
                    external_packages=dict(env.external_packages),
                )
            validate_urls(cast("Environment", validation_env), FORMAT)

            if env_name not in environments:
                environments[env_name] = {
                    "channels": [{"url": ch} for ch in env.config.channels],
                    "packages": {},
                }

            platform_refs: list[dict[str, str]] = []

            for pkg in sorted(env.explicit_packages, key=lambda p: p.name):
                platform_refs.append({"conda": pkg.url})
                if pkg.url not in seen_urls:
                    package_kwargs = {"conda": pkg.url}
                    for field in (
                        "sha256",
                        "md5",
                        "depends",
                        "constrains",
                        "features",
                        "track_features",
                        "license",
                        "license_family",
                        "size",
                        "python_site_packages_path",
                    ):
                        if value := pkg.get(field, None):
                            package_kwargs[field] = value
                    packages.append(
                        RattlerLockV6Package(**package_kwargs).model_dump(
                            exclude_none=True
                        )
                    )
                    seen_urls.add(pkg.url)

            for manager, urls in env.external_packages.items():
                for url in urls:
                    platform_refs.append({manager: url})

            environments[env_name]["packages"][platform] = platform_refs

        return {
            "version": LOCKFILE_VERSION,
            "environments": environments,
            "packages": packages,
        }


def render_lockfile(
    ctx: WorkspaceContext,
    resolved_envs: dict[str, ResolvedEnvironment],
    *,
    config: WorkspaceConfig | None = None,
    platforms: tuple[str, ...] | None = None,
    progress: Callable[[str, str], None] | None = None,
    skip_unsolvable: bool = False,
    on_skip: Callable[[str, str, SolveError], None] | None = None,
    solve_prefixes: Mapping[str, str | Path] | None = None,
    baseline_data: dict[str, Any] | None = None,
    update_targets: Mapping[tuple[str, str], set[str]] | None = None,
    dry_run: bool = False,
) -> str:
    """Solve workspace environments and serialize ``conda.lock`` content.

    Each environment in *resolved_envs* is solved for every platform it
    declares, intersected with *platforms* when given. When *config* is
    supplied, each ``(environment, platform)`` pair is resolved from the
    manifest just before solving so target-specific dependency tables only
    apply to the platform they declare. Serialisation is delegated to
    :func:`.export.multiplatform_export` so this function and ``conda
    export --format=conda-workspaces-lock-v1`` produce byte-identical
    output. Solver chatter is silenced inside
    :meth:`ResolvedEnvironment.solve_for_platform` itself, so the caller
    is free to render status through the optional *progress* callback
    without stdout bookkeeping.

    Fails fast by default: the first unsolvable ``(environment,
    platform)`` pair raises :class:`SolveError` with the platform
    named, and no lockfile is written.  When *skip_unsolvable* is
    true, solver failures on an individual pair are reported via
    *on_skip* (if given) and the lockfile continues with the remaining
    pairs; :class:`AllTargetsUnsolvableError` is raised only if every
    pair fails.  Non-solver errors (missing channel, invalid manifest,
    etc.) always abort.

    Returns the serialized lockfile without writing it. When *dry_run* is
    true, solver package-cache writes use disposable storage.
    """
    from conda.common.serialize.yaml import dumps as yaml_dumps
    from conda.models.environment import EnvironmentConfig

    from .export import multiplatform_export
    from .resolver import resolve_environment

    host_platform = ctx.platform
    envs: list[_SolvedEnvironment] = []
    failures: list[SolveError] = []

    if update_targets is not None and baseline_data is None:
        raise ValueError("Selective lock updates require baseline lockfile data")
    baseline = {} if baseline_data is None else baseline_data

    solved_targets: set[tuple[str, str]] = set()
    with isolated_package_cache(dry_run):
        for name, resolved in resolved_envs.items():
            declared = sorted(set(resolved.platforms or [host_platform]))
            if update_targets is not None:
                targets = [
                    target for target in declared if update_targets.get((name, target))
                ]
            elif platforms is None:
                targets = declared
            else:
                targets = []
                for requested in platforms:
                    try:
                        target = resolved.resolve_platform_name(requested, declared)
                    except PlatformError:
                        continue
                    if target not in targets:
                        targets.append(target)
            if not targets:
                continue
            for target in targets:
                if progress is not None:
                    progress(name, target)
                target_resolved = (
                    resolve_environment(config, name, target)
                    if config is not None
                    else resolved
                )
                package_platform = target_resolved.platform_subdir(target)
                channels = tuple(str(ch) for ch in target_resolved.channels)
                try:
                    if update_targets is not None:
                        solved_targets.add((name, target))
                        update_names = update_targets[(name, target)]
                        requested_specs = list(
                            target_resolved.conda_dependencies.values()
                        )
                        from .envs import _build_pypi_specs

                        requested_specs.extend(_build_pypi_specs(target_resolved))
                        with tempfile.TemporaryDirectory(
                            prefix="conda-workspaces-lock-update-"
                        ) as temp_dir:
                            prefix = Path(temp_dir)
                            try:
                                records = CondaLockLoader.seed_prefix_from_data(
                                    baseline,
                                    name,
                                    target,
                                    prefix,
                                    requested_specs,
                                    package_platform=package_platform,
                                )
                            except ValueError as exc:
                                raise LockfileIntegrityError(
                                    lockfile_path(ctx),
                                    str(exc),
                                ) from exc
                            installed_names = {record.name for record in records}
                            missing = update_names - installed_names
                            if missing:
                                names = ", ".join(sorted(missing))
                                raise LockfileIntegrityError(
                                    lockfile_path(ctx),
                                    f"environment {name!r} on {target!r} is missing"
                                    f" requested roots: {names}",
                                )
                            records = target_resolved.solve_for_platform(
                                package_platform,
                                prefix=prefix,
                                update_names=update_names,
                            )
                    else:
                        solve_prefix = (
                            solve_prefixes.get(
                                name, ctx.env_prefix(target_resolved.name)
                            )
                            if solve_prefixes is not None
                            else ctx.env_prefix(target_resolved.name)
                        )
                        records = target_resolved.solve_for_platform(
                            package_platform,
                            prefix=solve_prefix,
                        )
                except SolveError as exc:
                    if update_targets is not None or not skip_unsolvable:
                        raise
                    failures.append(exc)
                    if on_skip is not None:
                        on_skip(name, target, exc)
                    continue
                envs.append(
                    _SolvedEnvironment(
                        name=name,
                        platform=target,
                        package_platform=package_platform,
                        config=EnvironmentConfig(channels=channels),
                        explicit_packages=records,
                    )
                )

    if update_targets is not None:
        missing_targets = {
            target for target, names in update_targets.items() if names
        } - solved_targets
        if missing_targets:
            targets = ", ".join(
                f"{name}/{platform}" for name, platform in sorted(missing_targets)
            )
            raise ValueError(f"Selective lock targets are not declared: {targets}")
        updated = CondaLockLoader.replace_solutions(baseline, envs)
        if config is not None:
            declared_platforms = {
                platform
                for resolved in resolved_envs.values()
                for platform in (resolved.platforms or [host_platform])
            }
            for platform in declared_platforms:
                status = check_lockfile_satisfiability(config, updated, platform)
                if status.status != LockfileStatus.UP_TO_DATE:
                    raise LockfileStaleError(
                        config.manifest_path,
                        lockfile_path(ctx),
                        reason=status.reason,
                    )
        return yaml_dumps(updated)

    if failures and not envs:
        raise AllTargetsUnsolvableError(failures)

    return multiplatform_export(cast("Iterable[Environment]", envs))


def generate_lockfile(
    ctx: WorkspaceContext,
    resolved_envs: dict[str, ResolvedEnvironment],
    *,
    config: WorkspaceConfig | None = None,
    platforms: tuple[str, ...] | None = None,
    progress: Callable[[str, str], None] | None = None,
    skip_unsolvable: bool = False,
    on_skip: Callable[[str, str, SolveError], None] | None = None,
    output_path: Path | None = None,
    dry_run: bool = False,
    solve_prefixes: Mapping[str, str | Path] | None = None,
) -> Path:
    """Solve workspace environments and write their ``conda.lock``.

    When *output_path* is given, the lockfile is written there instead
    of the default ``<workspace>/conda.lock``. Matrix CI runners use this
    to emit per-platform fragments that a coordinator job later combines
    with :func:`merge_lockfiles`. When *dry_run* is true, solving and
    serialization still run without creating or changing the output.
    """
    path = output_path if output_path is not None else lockfile_path(ctx)
    validate_lockfile_output(ctx, path)
    content = render_lockfile(
        ctx,
        resolved_envs,
        config=config,
        platforms=platforms,
        progress=progress,
        skip_unsolvable=skip_unsolvable,
        on_skip=on_skip,
        solve_prefixes=solve_prefixes,
        dry_run=dry_run,
    )
    if dry_run:
        return path
    return write_lockfile(ctx, content, output_path=path)


def write_lockfile(
    ctx: WorkspaceContext,
    content: str,
    *,
    output_path: Path | None = None,
) -> Path:
    """Validate and write already-rendered canonical lockfile content."""
    path = output_path if output_path is not None else lockfile_path(ctx)
    validate_lockfile_output(ctx, path)
    atomic_write_text(path, content)
    return path


def merge_lockfiles(
    paths: Sequence[Path],
    ctx: WorkspaceContext,
    *,
    dry_run: bool = False,
) -> Path:
    """Merge per-platform ``conda.lock`` fragments into a single lockfile.

    Designed for CI matrix pipelines that split locking across runners:
    each runner produces a fragment for one platform (typically via
    ``conda workspace lock --platform <subdir> --output
    conda.lock.<subdir>``), and a coordinator job stitches them back
    into a single ``<workspace>/conda.lock`` with this function.

    Every fragment must be a ``version: 1`` lockfile.  When the current
    workspace manifest declares the fragment environment, its channel
    list is canonical: fragments may omit unused manifest channels, but
    any channels they do declare must be an ordered subset of the
    manifest's list.  Fragment environments not declared in the manifest
    keep the stricter legacy rule and must agree on their ``channels``
    list entry-for-entry and in the same order.  Two fragments may not
    both carry entries for the same ``(environment, platform)`` pair —
    overlapping platforms indicate a misconfigured pipeline rather than
    a legitimate merge.  Any violation raises
    :class:`LockfileMergeError` and nothing is written.

    The merge happens at the YAML layer — going through
    :class:`CondaLockLoader` would force
    :func:`conda_lockfiles.records_from_conda_urls.records_from_conda_urls`
    to fetch every package to populate :class:`PackageRecord` objects,
    which defeats the purpose of merging cached fragments.  We stitch
    the dicts back together and hand them to conda's YAML dumper.

    The output is byte-stable with a single-run
    :func:`generate_lockfile` call over the same inputs: environments
    are walked in first-seen order across fragments, platforms in
    alphabetical order within each environment, and top-level
    ``packages`` are emitted in the same order
    :meth:`CondaLockLoader.compose` would produce. Duplicate top-level
    records for the same package URL are accepted only when their
    metadata matches exactly.

    When *dry_run* is true, every fragment is validated and the merged
    data is serialized, but the canonical lockfile remains unchanged.

    Returns the path to the merged lockfile.
    """
    from conda.common.serialize.yaml import dump as yaml_dump
    from conda_lockfiles.load_yaml import load_yaml

    if not paths:
        raise LockfileMergeError("no lockfile fragments were supplied")

    out_path = lockfile_path(ctx)
    validate_lockfile_output(ctx, out_path)

    env_order: list[str] = []
    env_channels: dict[str, list[dict[str, Any]]] = {}
    env_platforms: dict[str, dict[str, list[dict[str, Any]]]] = {}
    seen_pairs: dict[tuple[str, str], Path] = {}
    packages_by_url: dict[str, dict[str, Any]] = {}
    package_sources_by_url: dict[str, Path] = {}
    manifest_env_channels: dict[str, list[dict[str, str]]] = {}
    manifest_env_channel_urls: dict[str, list[str]] = {}

    for env_name, env_obj in ctx.config.environments.items():
        entries = _manifest_channel_entries(ctx.config, env_obj)
        manifest_env_channels[env_name] = entries
        manifest_env_channel_urls[env_name] = _normalize_channel_urls(
            entry["url"] for entry in entries
        )

    for path in paths:
        if not path.is_file():
            raise LockfileMergeError(f"fragment '{path}' does not exist")
        data = load_yaml(path)
        version = data.get("version")
        if version != LOCKFILE_VERSION:
            raise LockfileMergeError(
                f"fragment '{path}' has version {version!r}, "
                f"expected {LOCKFILE_VERSION}"
            )
        for record in data.get("packages", []) or []:
            url = record.get("url") or record.get("conda") or record.get("pypi")
            if not url:
                continue
            existing = packages_by_url.get(url)
            if existing is None:
                packages_by_url[url] = record
                package_sources_by_url[url] = path
            elif existing != record:
                raise LockfileMergeError(
                    f"fragment '{path}' has a conflicting package record for "
                    f"URL '{url}'",
                    hints=[
                        "The same package URL must have identical top-level"
                        " metadata in every fragment.",
                        f"The first record came from '{package_sources_by_url[url]}'.",
                    ],
                )

        for env_name, env_data in (data.get("environments") or {}).items():
            channels = list(env_data.get("channels") or [])
            manifest_channels = manifest_env_channels.get(env_name)
            if manifest_channels is not None:
                manifest_urls = manifest_env_channel_urls[env_name]
                fragment_urls = _normalize_channel_urls(
                    str(entry.get("url", "")) for entry in channels
                )
                next_index = 0
                for url in fragment_urls:
                    try:
                        next_index = manifest_urls.index(url, next_index) + 1
                    except ValueError as exc:
                        raise LockfileMergeError(
                            f"environment '{env_name}' channels differ between "
                            f"fragment '{path}' and the manifest",
                            hints=[
                                "Every fragment channel list must be an ordered"
                                " subset of the manifest channel list for a"
                                " shared environment.",
                            ],
                        ) from exc
                channels = list(manifest_channels)
            existing = env_channels.get(env_name)
            if existing is None:
                env_order.append(env_name)
                env_channels[env_name] = channels
                env_platforms[env_name] = {}
            elif existing != channels:
                raise LockfileMergeError(
                    f"environment '{env_name}' channels differ between "
                    f"fragments; '{path}' disagrees with an earlier fragment",
                    hints=[
                        "Every fragment must declare the same channel list"
                        " (same entries, same order) for a shared environment.",
                    ],
                )
            for platform, refs in (env_data.get("packages") or {}).items():
                pair = (env_name, platform)
                if pair in seen_pairs:
                    raise LockfileMergeError(
                        f"environment '{env_name}' on platform "
                        f"'{platform}' is present in both "
                        f"'{seen_pairs[pair]}' and '{path}'",
                        hints=[
                            "Each (environment, platform) pair must come"
                            " from exactly one fragment.",
                        ],
                    )
                seen_pairs[pair] = path
                package_platform = ctx.config.platform_subdir(platform)
                try:
                    channel_urls = CondaLockLoader.channel_urls_for_env_data(
                        {"channels": channels},
                        package_platform,
                    )
                except ValueError as exc:
                    raise LockfileMergeError(
                        f"environment '{env_name}' channels in fragment "
                        f"'{path}' cannot be resolved"
                    ) from exc

                validated_refs: list[dict[str, Any]] = []
                for ref in refs or []:
                    if not isinstance(ref, dict):
                        continue
                    url = ref.get("conda")
                    if not url:
                        validated_refs.append(ref)
                        continue
                    if not isinstance(url, str):
                        raise LockfileMergeError(
                            f"environment '{env_name}' contains an invalid "
                            f"package ref on '{platform}' in fragment '{path}'"
                        )
                    if not CondaLockLoader.url_matches_channel(url, channel_urls):
                        raise LockfileMergeError(
                            f"package URL '{url}' in environment '{env_name}' "
                            f"on '{platform}' is not under any declared channel"
                        )
                    record = packages_by_url.get(url)
                    if record is None:
                        raise LockfileMergeError(
                            f"package URL '{url}' in environment '{env_name}' "
                            f"on '{platform}' has no top-level package record"
                        )
                    try:
                        CondaLockLoader.digest_fragment_for_record(record, url)
                    except ValueError as exc:
                        raise LockfileMergeError(str(exc)) from exc
                    validated_refs.append(ref)
                env_platforms[env_name][platform] = validated_refs

    # Rebuild top-level ``packages`` in the same order
    # :meth:`CondaLockLoader.compose` would produce for a single-run
    # solve: iterate envs in first-seen order, platforms alphabetically,
    # then each platform's refs (already sorted by package name by the
    # producing fragment).
    merged_packages: list[dict[str, Any]] = []
    emitted_urls: set[str] = set()
    for env_name in env_order:
        for platform in sorted(env_platforms[env_name]):
            for ref in env_platforms[env_name][platform]:
                url = ref.get("conda")
                if not url or url in emitted_urls:
                    continue
                record = packages_by_url.get(url)
                if record is not None:
                    merged_packages.append(record)
                    emitted_urls.add(url)

    merged = {
        "version": LOCKFILE_VERSION,
        "environments": {
            name: {
                "channels": env_channels[name],
                "packages": {
                    platform: env_platforms[name][platform]
                    for platform in sorted(env_platforms[name])
                },
            }
            for name in env_order
        },
        "packages": merged_packages,
    }

    buf = io.StringIO()
    yaml_dump(merged, buf)
    if dry_run:
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(buf.getvalue(), encoding="utf-8")
    return out_path


def validate_lockfile_output(ctx: WorkspaceContext, path: Path) -> None:
    """Reject lock outputs that could overwrite the selected manifest."""
    manifest_path = Path(ctx.config.manifest_path)
    if output_paths_collide(path, manifest_path):
        raise ValueError("Lockfile output cannot overwrite the workspace manifest.")
    validate_file_output(path)


def install_from_lockfile(
    ctx: WorkspaceContext,
    env_name: str,
    *,
    prefix: Path | None = None,
    target_prefix_override: str | Path | None = None,
    dry_run: bool = False,
) -> None:
    """Install an environment from ``conda.lock``.

    Reads the lockfile via :class:`CondaLockLoader`, extracts the
    package list for *env_name* on the current platform, downloads the
    exact packages, removes conda packages absent from the lock, and
    installs them into the environment prefix without a solver.
    Requested-spec history is reconciled to the resolved manifest
    roots.

    When *dry_run* is true, the lockfile and selected package references
    are validated without creating or changing the target prefix.

    Raises ``LockfileNotFoundError`` if the lockfile is missing or does
    not contain the requested environment/platform.
    """
    from conda.base.context import context as conda_context
    from conda.core.link import PrefixSetup, UnlinkLinkTransaction
    from conda.core.prefix_data import PrefixData
    from conda.history import History
    from conda.misc import (
        get_package_records_from_explicit,
        install_explicit_packages,
    )
    from conda.models.match_spec import MatchSpec

    path = lockfile_path(ctx)
    if not path.is_file():
        raise LockfileNotFoundError("(all)", path)

    loader = CondaLockLoader(path)
    lock_platform = ctx.platform
    package_platform = ctx.platform
    resolved: ResolvedEnvironment | None = None
    try:
        from .resolver import resolve_environment

        try:
            resolved = resolve_environment(ctx.config, env_name)
            lock_platform = ctx.config.resolve_platform_name(
                ctx.platform,
                resolved.platforms or ctx.config.platforms,
            )
            package_platform = ctx.config.platform_subdir(lock_platform)
        except (EnvironmentNotFoundError, PlatformError):
            pass
        urls = loader.explicit_package_specs_for(
            lock_platform,
            env_name,
            package_platform=package_platform,
        )
    except (ValueError, OSError) as exc:
        raise LockfileNotFoundError(env_name, path) from exc

    install_prefix = prefix or ctx.env_prefix(env_name)
    validate_directory_output(install_prefix)
    if dry_run:
        return
    install_prefix.mkdir(parents=True, exist_ok=True)

    override = (
        conda_context._override(
            "target_prefix_override",
            str(target_prefix_override),
        )
        if target_prefix_override is not None
        else nullcontext()
    )

    with override:
        records = cast(
            "list[PackageRecord]",
            list(get_package_records_from_explicit(urls)),
        )
        requested_specs: list[MatchSpec] | None = None
        unlocked_requested_names: set[str | None] = set()
        if resolved is not None:
            from .envs import _build_pypi_specs

            requested_specs = list(resolved.conda_dependencies.values())
            requested_specs.extend(_build_pypi_specs(resolved))
            for dependency in resolved.pypi_dependencies.values():
                if dependency.path:
                    spec = MatchSpec(dependency.name)
                    requested_specs.append(spec)
                    unlocked_requested_names.add(spec.name)

        prefix_data = PrefixData(str(install_prefix))
        if prefix_data.is_environment():
            locked_names = {record.name for record in records}
            requested_names = (
                {spec.name for spec in requested_specs}
                if requested_specs is not None
                else None
            )
            stale_requests = (
                tuple(
                    spec
                    for name, spec in History(str(install_prefix))
                    .get_requested_specs_map()
                    .items()
                    if name not in requested_names
                )
                if requested_names is not None
                else ()
            )
            extras = tuple(
                record
                for record in prefix_data.iter_records()
                if record.name not in locked_names
                and record.name not in unlocked_requested_names
            )
            if extras or stale_requests:
                UnlinkLinkTransaction(
                    PrefixSetup(
                        str(install_prefix),
                        extras,
                        (),
                        stale_requests,
                        (),
                        (),
                    )
                ).execute()

        install_explicit_packages(
            package_cache_records=records,
            prefix=str(install_prefix),
            requested_specs=(
                [str(spec) for spec in requested_specs]
                if requested_specs is not None
                else None
            ),
        )
    sys.stdout.flush()
