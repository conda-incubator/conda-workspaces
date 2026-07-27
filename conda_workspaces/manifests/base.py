"""Abstract base class for manifest parsers (workspaces and tasks)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import tomlkit
from conda.base.constants import KNOWN_SUBDIRS
from conda.exceptions import InvalidMatchSpec
from packaging.requirements import InvalidRequirement, Requirement

from ..exceptions import (
    ManifestExistsError,
    TaskNotFoundError,
    TaskParseError,
    WorkspaceParseError,
)
from ..models import (
    Channel,
    MatchSpec,
    PyPIDependency,
    WorkspaceConfig,
    has_match_spec_url_credentials,
    has_url_credentials,
    has_url_credentials_in_data,
    redact_channel_name,
    redact_channel_url,
    redact_url_text,
)
from ..parsing import (
    decode_limited_text,
    read_limited_text,
    validate_document_limits,
)
from ..paths import atomic_write_text, read_regular_file_bytes_with_generation

_PYPI_NAME_TAIL_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$")
MATCH_SPEC_FIELD_ALIASES: dict[str, str] = {
    "version": "version",
    "build": "build",
    "build-number": "build_number",
    "build_number": "build_number",
    "channel": "channel",
    "subdir": "subdir",
    "md5": "md5",
    "sha256": "sha256",
    "url": "url",
    "fn": "fn",
    "file-name": "fn",
    "license": "license",
    "license-family": "license_family",
    "license_family": "license_family",
    "features": "features",
    "track-features": "track_features",
    "track_features": "track_features",
}
MATCH_SPEC_TOML_FIELDS: dict[str, str] = {
    field: "file-name" if field == "fn" else field.replace("_", "-")
    for field in dict.fromkeys(MATCH_SPEC_FIELD_ALIASES.values())
}
MAX_MANIFEST_BYTES = 16 * 1024**2
MAX_MANIFEST_DEPTH = 128
MAX_MANIFEST_COLLECTION_ITEMS = 100_000
MAX_MANIFEST_ITEMS = 1_000_000

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path
    from typing import Any, ClassVar

    from conda.models.environment import Environment
    from tomlkit.container import Container
    from tomlkit.items import InlineTable, Table

    from ..models import Task
    from ..paths import FileGeneration


def match_spec_to_toml(spec: MatchSpec) -> str | InlineTable:
    """Return a lossless workspace TOML value for a conda ``MatchSpec``.

    This is a module-level function because ``MatchSpec`` is owned by conda and
    the serializer is shared by parsers, importers, mutations, and exporters.
    """
    if has_match_spec_url_credentials(spec, include_channel=False):
        raise InvalidMatchSpec(
            spec.name or "package",
            "credential-bearing package fields cannot be written to workspace"
            " manifests. Configure authentication outside the manifest, then"
            " remove embedded authentication, Anaconda /t/<token>/ segments,"
            " queries, and fragments",
        )

    unsupported = [
        field
        for field in MatchSpec.FIELD_NAMES
        if field not in MATCH_SPEC_TOML_FIELDS
        and field != "name"
        and spec.get_raw_value(field) is not None
    ]
    if spec.optional is not False:
        unsupported.append("optional")
    if spec.target is not None:
        unsupported.append("target")
    if unsupported:
        fields = ", ".join(unsupported)
        raise InvalidMatchSpec(
            spec.name or "package",
            f"field(s) cannot be represented in a workspace manifest: {fields}",
        )

    subdir = spec.get_raw_value("subdir")
    if subdir is not None and subdir not in KNOWN_SUBDIRS:
        raise InvalidMatchSpec(
            spec.name or "package",
            f"subdir '{subdir}' is not a known conda platform",
        )

    fields: dict[str, Any] = {}
    for field, key in MATCH_SPEC_TOML_FIELDS.items():
        value = spec.get_raw_value(field)
        if value is None:
            continue
        if field == "channel":
            channel = Channel(value)
            original = spec.original_spec_str or ""
            prefix, separator, _ = original.rpartition("::")
            explicit_channel = (
                prefix
                if separator and re.match(r"(?i)[a-z][a-z0-9+.-]*://", prefix)
                else None
            )
            if explicit_channel is None:
                match = re.search(
                    r"(?:^|[\[,])\s*channel\s*=\s*(?:"
                    r"(?P<quote>['\"])(?P<quoted>.*?)(?P=quote)|"
                    r"(?P<unquoted>[^,'\"\]\s]+))",
                    original,
                )
                if match is not None:
                    raw_channel = match.group("quoted") or match.group("unquoted")
                    if re.match(r"(?i)[a-z][a-z0-9+.-]*://", raw_channel):
                        explicit_channel = raw_channel
            if explicit_channel is not None:
                value = redact_channel_name(explicit_channel)
            elif spec.original_spec_str is None and channel.base_url:
                value = redact_channel_url(channel)
            else:
                value = next(
                    candidate
                    for candidate in (
                        channel.canonical_name,
                        channel.name,
                        str(channel),
                    )
                    if candidate and Channel(candidate) == channel
                )
                value = redact_channel_name(str(value))
        elif field == "build_number":
            value = str(value)
        elif isinstance(value, frozenset):
            value = sorted(value)
        fields[key] = value

    if not fields:
        return "*"
    if list(fields) == ["version"]:
        return fields["version"]
    table = tomlkit.inline_table()
    table.update(fields)
    return table


class ManifestParser(ABC):
    """Interface that every manifest parser must implement.

    Each parser handles one file format (``conda.toml``, ``pixi.toml``,
    or ``pyproject.toml``).  Subclasses declare which files they can
    handle via *filenames* and a short *format_alias* (``"conda"`` /
    ``"pixi"`` / ``"pyproject"``) that the CLI uses for ``--format``
    values.  The registry in :mod:`conda_workspaces.manifests` uses
    these to auto-detect the right parser and to resolve ``--format``
    aliases to the parser that owns the matching filename.

    A single parser instance handles both workspace configuration and
    task definitions from the same file.
    """

    format_alias: ClassVar[str] = ""
    filenames: ClassVar[tuple[str, ...]] = ()
    #: Canonical ``conda_environment_exporters`` plugin name.  Empty
    #: disables exporter registration for that parser (see
    #: :mod:`conda_workspaces.plugin`).
    exporter_format: ClassVar[str] = ""
    #: Optional user-friendly aliases for the exporter plugin (e.g.
    #: ``("conda",)`` for ``conda-toml``).  Empty tuple is fine.
    exporter_aliases: ClassVar[tuple[str, ...]] = ()
    rich_platform_system_requirement_keys: ClassVar[set[str]] = {
        "archspec",
        "cuda",
        "glibc",
        "libc",
        "linux",
        "macos",
        "osx",
        "win",
        "windows",
    }
    system_requirement_aliases: ClassVar[dict[str, str]] = {
        "libc": "glibc",
        "macos": "osx",
        "windows": "win",
    }

    @staticmethod
    def read_manifest_text(path: Path) -> str:
        """Read one repository manifest under the configured byte limit."""
        return read_limited_text(
            path,
            maximum_bytes=MAX_MANIFEST_BYTES,
            label="Manifest TOML",
        )

    @staticmethod
    def read_manifest_text_with_generation(path: Path) -> tuple[str, FileGeneration]:
        """Read a mutable manifest without links and return its generation."""
        content, generation = read_regular_file_bytes_with_generation(
            path,
            maximum_bytes=MAX_MANIFEST_BYTES,
            label="Manifest TOML",
        )
        return decode_limited_text(
            content,
            maximum_bytes=MAX_MANIFEST_BYTES,
            label="Manifest TOML",
        ), generation

    @staticmethod
    def parse_toml_text(content: str) -> tomlkit.TOMLDocument:
        """Parse manifest TOML text under explicit resource limits."""
        content = decode_limited_text(
            content,
            maximum_bytes=MAX_MANIFEST_BYTES,
            label="Manifest TOML",
        )
        document = tomlkit.loads(content)
        validate_document_limits(
            document.unwrap(),
            label="Manifest TOML",
            maximum_depth=MAX_MANIFEST_DEPTH,
            maximum_collection_items=MAX_MANIFEST_COLLECTION_ITEMS,
            maximum_items=MAX_MANIFEST_ITEMS,
        )
        return document

    @classmethod
    def parse_toml_text_with_redacted_errors(
        cls,
        content: str,
        path: Path,
    ) -> tomlkit.TOMLDocument:
        """Parse mutable manifest text without exposing sensitive diagnostics."""
        try:
            return cls.parse_toml_text(content)
        except Exception as exc:
            raise WorkspaceParseError(path, redact_url_text(str(exc))) from exc

    @classmethod
    def load_toml(cls, path: Path) -> tomlkit.TOMLDocument:
        """Read and parse one repository manifest under explicit limits."""
        return cls.parse_toml_text(cls.read_manifest_text(path))

    @classmethod
    def load_toml_with_generation(
        cls,
        path: Path,
    ) -> tuple[tomlkit.TOMLDocument, FileGeneration]:
        """Read a mutable manifest and retain the generation to replace."""
        content, generation = cls.read_manifest_text_with_generation(path)
        return cls.parse_toml_text_with_redacted_errors(content, path), generation

    @property
    def manifest_filename(self) -> str:
        """Canonical filename this parser reads and writes.

        The first entry in :attr:`filenames` — e.g. ``"conda.toml"``
        for :class:`CondaTomlParser`.  Used by :meth:`manifest_path` and
        the ``conda workspace init`` / ``quickstart`` CLI paths so the
        format-to-filename mapping lives in exactly one place.
        """
        return self.filenames[0]

    def manifest_path(self, root: Path) -> Path:
        """Return the manifest path this parser would (or did) write inside *root*."""
        return root / self.manifest_filename

    @classmethod
    def for_format_alias(cls, alias: str) -> ManifestParser:
        """Return the registered parser whose :attr:`format_alias` matches *alias*.

        Used by ``conda workspace init`` / ``quickstart`` to turn a
        ``--format`` value like ``"pyproject"`` / ``"conda"`` /
        ``"pixi"`` into the parser (and therefore the filename) it
        implies.  Raises :class:`ValueError` when no parser claims
        *alias*.  The companion lookup for
        ``conda workspace export --format`` is
        :meth:`for_exporter_format` — that side matches the longer
        ``conda_environment_exporters`` plugin name
        (``"pyproject-toml"``, ``"conda-toml"``, ``"pixi-toml"``)
        which is stored on :attr:`exporter_format`.  The registry is
        :data:`conda_workspaces.manifests._PARSERS`.
        """
        from . import _PARSERS

        for parser in _PARSERS:
            if parser.format_alias == alias:
                return parser
        known = sorted(p.format_alias for p in _PARSERS if p.format_alias)
        raise ValueError(
            f"Unknown manifest format {alias!r}; expected one of: {', '.join(known)}"
        )

    @classmethod
    def resolve_source(cls, source: Path) -> Path:
        """Resolve *source* (directory or file) to a concrete manifest path.

        Directories are walked via
        :func:`conda_workspaces.manifests.detect_workspace_file`; files
        are returned as-is.  Raises :class:`FileNotFoundError` when
        *source* does not exist and
        :class:`conda_workspaces.exceptions.WorkspaceNotFoundError`
        when the directory contains no recognisable manifest.
        """
        from . import detect_workspace_file

        if not source.exists():
            raise FileNotFoundError(source)
        return detect_workspace_file(source) if source.is_dir() else source

    @classmethod
    def copy_manifest(cls, source: Path, dest_dir: Path) -> Path:
        """Copy the manifest at *source* into *dest_dir*; return the target path.

        *source* may be a directory (walked via :meth:`resolve_source`)
        or a manifest file.  Raises :class:`FileNotFoundError`,
        :class:`conda_workspaces.exceptions.WorkspaceNotFoundError`, or
        :class:`conda_workspaces.exceptions.ManifestExistsError` as
        appropriate; callers layer their own dry-run / console policy
        on top.
        """
        manifest = cls.resolve_source(source)
        target = dest_dir / manifest.name
        if target.exists() or target.is_symlink():
            raise ManifestExistsError(target)
        atomic_write_text(
            target,
            cls.read_manifest_text(manifest),
            expected_identity=None,
        )
        return target

    def write_workspace_stub(
        self,
        base_dir: Path,
        name: str,
        channels: list[str],
        platforms: list[str],
    ) -> tuple[Path, str]:
        """Create a minimal workspace manifest under *base_dir*.

        Writes a fresh TOML document with ``[workspace]`` and an empty
        ``[dependencies]`` table at :meth:`manifest_path` and returns
        ``(path, "Created")``.  Raises :class:`ManifestExistsError` if
        the target file is already present — subclasses that share
        their file with other tooling (see
        :class:`PyprojectTomlParser`) override this method to append
        their configuration under a nested table instead of refusing
        outright, and report ``"Updated"`` when they did so.
        """
        path = self.manifest_path(base_dir)
        if path.exists() or path.is_symlink():
            raise ManifestExistsError(path)

        doc = tomlkit.document()
        ws = tomlkit.table()
        ws.add("name", name)
        ws.add("channels", channels)
        ws.add("platforms", platforms)
        doc.add("workspace", ws)
        doc.add("dependencies", tomlkit.table())

        atomic_write_text(
            path,
            tomlkit.dumps(doc),
            expected_identity=None,
        )
        return path, "Created"

    def merge_export(self, existing_path: Path, exported: str) -> str:
        """Return *exported* ready to write into an existing *existing_path*.

        The default implementation returns *exported* unchanged —
        ``conda.toml`` and ``pixi.toml`` are manifests we own
        end-to-end, so regenerating them from an environment is a
        full replacement (same as ``conda export -f
        environment.yaml`` overwriting an existing environment.yaml).

        :class:`PyprojectTomlParser` overrides this to splice the
        exporter's ``[tool.conda]`` subtree into the existing
        ``pyproject.toml`` document without disturbing peer tables
        (``[project]``, ``[build-system]``, ``[tool.ruff]`` etc.),
        because ``pyproject.toml`` is a shared manifest owned by the
        Python ecosystem.  Called from :mod:`conda_workspaces.cli.workspace.export`
        only when ``--file`` points to an existing file, so a fresh
        export still writes the exporter output verbatim.
        """
        return exported

    def merge_export_text(self, existing: str, exported: str) -> str:
        """Merge *exported* with a previously captured existing generation."""
        return exported

    def parse_system_requirements(
        self,
        requirements: Mapping[str, Any],
    ) -> dict[str, str]:
        """Parse Pixi-facing system requirements into conda virtual names.

        Pixi exposes TOML names like ``libc`` / ``macos`` / ``windows``;
        conda virtual packages use ``__glibc`` / ``__osx`` / ``__win``.
        The workspace model stores bare conda names while preserving raw
        ``__name`` escape hatches for callers that already use virtual
        package names.
        """
        parsed: dict[str, str] = {}
        for raw_name, raw_value in requirements.items():
            name = str(raw_name)
            prefixed = name.startswith("__")
            bare_name = name[2:] if prefixed else name

            if bare_name == "libc" and isinstance(raw_value, dict):
                family = str(raw_value.get("family", "glibc"))
                version = raw_value.get("version")
                bare_name = self.system_requirement_aliases.get(family, family)
                raw_value = "" if version is None else version
            else:
                bare_name = self.system_requirement_aliases.get(bare_name, bare_name)

            parsed[f"__{bare_name}" if prefixed else bare_name] = str(raw_value)
        return parsed

    def parse_workspace_platforms(
        self,
        raw: Iterable[Any],
        path: Path,
    ) -> tuple[list[str], dict[str, str], dict[str, dict[str, str]]]:
        """Parse Pixi-compatible workspace platform entries.

        Bare strings remain plain conda subdirs.  Inline tables can
        add virtual package requirements and optional Pixi rich-platform
        names, e.g. ``{ name = "linux-64-cuda", platform = "linux-64",
        cuda = "12" }``.  The returned platform list contains the
        declared names used by features and lockfiles; ``platform_subdirs``
        records the concrete conda subdir each name solves against.
        """
        platforms: list[str] = []
        platform_subdirs: dict[str, str] = {}
        platform_system_requirements: dict[str, dict[str, str]] = {}

        def add_platform(
            name: str,
            subdir: str,
            requirements: dict[str, str],
        ) -> None:
            if name in platform_subdirs:
                raise WorkspaceParseError(
                    path,
                    f"Duplicate workspace platform name {name!r}.",
                )
            platforms.append(name)
            platform_subdirs[name] = subdir
            if requirements:
                platform_system_requirements[name] = requirements

        for item in raw:
            if isinstance(item, str):
                add_platform(item, item, {})
                continue
            if not isinstance(item, dict):
                raise WorkspaceParseError(
                    path,
                    "[workspace].platforms entries must be strings or inline tables",
                )

            raw_subdir = item.get("platform")
            raw_name = item.get("name")
            if raw_subdir is None and raw_name is None:
                raise WorkspaceParseError(
                    path,
                    "Rich platform entries must set `platform` or `name`.",
                )
            requirements = {
                key: value
                for key, value in item.items()
                if key in self.rich_platform_system_requirement_keys
                or str(key).startswith("__")
            }
            parsed_requirements = self.parse_system_requirements(requirements)

            if raw_subdir is None:
                subdir = str(raw_name)
                if subdir not in KNOWN_SUBDIRS:
                    raise WorkspaceParseError(
                        path,
                        "Rich platform entries with custom `name` values "
                        "must also set `platform`.",
                    )
                name = subdir
            else:
                subdir = str(raw_subdir)
                name = (
                    str(raw_name)
                    if raw_name is not None
                    else WorkspaceConfig.synthesize_platform_name(
                        subdir,
                        parsed_requirements,
                    )
                )

            if name in KNOWN_SUBDIRS and name != subdir:
                raise WorkspaceParseError(
                    path,
                    "Rich platform entries named after a conda subdir must "
                    "use the same `platform` value.",
                )

            add_platform(name, subdir, parsed_requirements)

        return platforms, platform_subdirs, platform_system_requirements

    @classmethod
    def for_exporter_format(cls, name: str) -> ManifestParser | None:
        """Return the registered parser whose :attr:`exporter_format` matches *name*.

        Companion to :meth:`for_format_alias` for the
        ``conda_environment_exporters`` plugin side: ``conda workspace
        export --format <name>`` uses the exporter plugin name (e.g.
        ``pyproject-toml``), which is stored on
        :attr:`exporter_format` rather than :attr:`format_alias`.
        Returns ``None`` when *name* is not a manifest-format exporter
        — the CLI uses this to decide whether to route writes through
        :meth:`merge_export`, and a ``None`` result simply means "not
        one of ours, write verbatim".
        """
        from . import _PARSERS

        for parser in _PARSERS:
            if parser.exporter_format and parser.exporter_format == name:
                return parser
        return None

    def export(self, envs: Iterable[Environment]) -> str:
        """Serialize *envs* to this parser's manifest format.

        Produces a manifest that, when written to disk and parsed by
        :meth:`parse`, describes the same requested dependencies,
        channels, and declared platforms that *envs* carry.  Each
        :class:`~conda.models.environment.Environment` is one
        ``(name, platform)`` pair; *envs* must all share the same
        ``name`` (conda's
        :class:`~conda.plugins.types.CondaEnvironmentExporter` hook
        calls ``multiplatform_export`` with per-platform copies of
        the same logical environment).

        The default implementation writes top-level ``[workspace]``,
        ``[dependencies]``, ``[pypi-dependencies]``, and
        ``[target.<platform>.*]`` tables — the shape ``conda.toml``
        and ``pixi.toml`` share.  :class:`PyprojectTomlParser`
        overrides it to nest the same content under ``[tool.conda]``
        without disturbing the rest of the pyproject.  Used as the
        ``multiplatform_export`` callable on the exporter plugins
        registered from :mod:`conda_workspaces.plugin`.
        """
        envs = list(envs)
        data = self.manifest_data(envs)
        doc = tomlkit.document()
        self._emit_manifest(doc, data)
        return tomlkit.dumps(doc)

    def _emit_manifest(
        self, container: Container | Table, data: dict[str, Any]
    ) -> None:
        """Write the manifest tables produced by :meth:`manifest_data` into *container*.

        *container* is a tomlkit table (either a fresh ``TOMLDocument``
        or a nested ``[tool.conda]`` table); :meth:`export` hands it in
        already positioned at the root of the manifest.  Kept as a
        separate method so :class:`PyprojectTomlParser.export` can
        reuse the exact same writer after it has set up the outer
        ``[tool.conda]`` wrapper.
        """
        ws = tomlkit.table()
        if data["name"] is not None:
            ws.add("name", data["name"])
        ws.add("channels", data["channels"])
        ws.add("platforms", data["platforms"])
        container.add("workspace", ws)

        deps_table = tomlkit.table()
        for name, spec in sorted(data["conda_deps"].items()):
            deps_table.add(name, spec)
        container.add("dependencies", deps_table)

        if data["pypi_deps"]:
            pypi_table = tomlkit.table()
            for name, spec in sorted(data["pypi_deps"].items()):
                pypi_table.add(name, spec)
            container.add("pypi-dependencies", pypi_table)

        target_data = data["target"]
        if any(target_data.values()):
            target = tomlkit.table(is_super_table=True)
            for platform in sorted(target_data):
                entry = target_data[platform]
                if not entry["conda"] and not entry["pypi"]:
                    continue
                platform_tbl = tomlkit.table()
                if entry["conda"]:
                    c = tomlkit.table()
                    for n, s in sorted(entry["conda"].items()):
                        c.add(n, s)
                    platform_tbl.add("dependencies", c)
                if entry["pypi"]:
                    p = tomlkit.table()
                    for n, s in sorted(entry["pypi"].items()):
                        p.add(n, s)
                    platform_tbl.add("pypi-dependencies", p)
                target.add(platform, platform_tbl)
            container.add("target", target)

    @classmethod
    def manifest_data(cls, envs: Iterable[Environment]) -> dict[str, Any]:
        """Fold one or more ``Environment`` objects into a manifest-shaped dict.

        Returns the data that :meth:`export` writers need, with the
        format-agnostic parts decided once:

        * ``name`` / ``platforms`` / ``channels`` describe the
          ``[workspace]`` table (platforms are the sorted union across
          *envs*; channels are taken from the first env — exporter
          callers pass the same channel list on every platform).
        * ``conda_deps`` / ``pypi_deps`` are the intersection across
          *envs* — specs that match by name *and* value on every
          platform, the ones a round-trip parse would put under the
          top-level ``[dependencies]`` / ``[pypi-dependencies]``
          tables.
        * ``target[<platform>]["conda"|"pypi"]`` holds the per-platform
          delta — specs that appear on some platforms but not others,
          or whose value differs across platforms.  A round-trip parse
          restores these under ``[target.<platform>.dependencies]`` /
          ``[target.<platform>.pypi-dependencies]``.

        Used by :meth:`export` (via :meth:`_emit_manifest`) and
        exposed as a classmethod so individual parsers and exporter
        plugin shims can drive the same folding logic without
        duplicating it.
        """
        envs = list(envs)
        if not envs:
            raise ValueError("At least one Environment is required for export.")

        name = next((env.name for env in envs if env.name), None)
        platforms = sorted({env.platform for env in envs})
        channels = [
            redact_channel_name(str(channel)) for channel in envs[0].config.channels
        ]

        # Per-platform specs as ``{name: manifest value}`` dicts symmetric with
        # ``WorkspaceDependencyResolver`` / ``toml.parse_pypi_dependencies``.
        # Conda fields and named PyPI direct URLs retain their source identity.
        # When a PyPI entry is not a valid PEP 508 string (e.g. the
        # ``"requests*"`` that
        # :meth:`~conda_workspaces.models.PyPIDependency.__str__`
        # emits for a ``requests = "*"`` manifest wildcard), fall
        # back to splitting name from specifier at the first
        # non-identifier character — matches what ``environment-yaml``
        # does in the same case: pass the input through as-is rather
        # than crashing.
        per_platform_conda: dict[str, dict[str, Any]] = {}
        per_platform_pypi: dict[str, dict[str, Any]] = {}
        for env in envs:
            conda_row: dict[str, Any] = {}
            requested_packages = list(env.requested_packages)
            if not requested_packages:
                for record in env.explicit_packages:
                    url = getattr(record, "url", None)
                    if not url:
                        raise InvalidMatchSpec(
                            getattr(record, "name", "package"),
                            "an exact package URL is required for manifest export",
                        )
                    digests = {
                        algorithm: value
                        for algorithm in ("sha256", "md5")
                        if (value := getattr(record, algorithm, None))
                    }
                    requested_packages.append(MatchSpec(MatchSpec(url), **digests))
            for spec in requested_packages:
                package_name = spec.get_exact_value("name")
                if not package_name:
                    raise InvalidMatchSpec(
                        str(spec),
                        "an exact package name is required for manifest export",
                    )
                conda_row[package_name] = match_spec_to_toml(spec)
            per_platform_conda[env.platform] = conda_row

            pypi_row: dict[str, Any] = {}
            for raw in env.external_packages.get("pip", []):
                try:
                    req = Requirement(raw)
                except InvalidRequirement:
                    match = _PYPI_NAME_TAIL_RE.match(raw.strip())
                    if match and match.group(2).strip() == "*":
                        name_part, tail = match.groups()
                        pypi_row[name_part] = tail.strip() or "*"
                        continue
                    raise ValueError(
                        "Cannot export an invalid PyPI dependency."
                    ) from None
                if req.marker is not None:
                    raise ValueError(
                        f"PyPI dependency '{req.name}' has an environment marker"
                        " that workspace manifests cannot represent."
                    )
                dependency = PyPIDependency(
                    name=req.name,
                    spec=str(req.specifier),
                    extras=tuple(sorted(req.extras)),
                    url=req.url,
                )
                pypi_row[req.name] = dependency.to_manifest_toml()
            per_platform_pypi[env.platform] = pypi_row

        common_conda = cls._intersect_rows(per_platform_conda)
        common_pypi = cls._intersect_rows(per_platform_pypi)

        target: dict[str, dict[str, dict[str, Any]]] = {}
        for platform in platforms:
            delta_conda = {
                n: s
                for n, s in per_platform_conda[platform].items()
                if common_conda.get(n) != s
            }
            delta_pypi = {
                n: s
                for n, s in per_platform_pypi[platform].items()
                if common_pypi.get(n) != s
            }
            target[platform] = {"conda": delta_conda, "pypi": delta_pypi}

        return {
            "name": name,
            "platforms": platforms,
            "channels": channels,
            "conda_deps": common_conda,
            "pypi_deps": common_pypi,
            "target": target,
        }

    @classmethod
    def _intersect_rows(
        cls,
        per_platform: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Return entries present on every platform with identical values."""
        if not per_platform:
            return {}
        platforms = list(per_platform)
        first = per_platform[platforms[0]]
        return {
            name: spec
            for name, spec in first.items()
            if all(per_platform[p].get(name) == spec for p in platforms[1:])
        }

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """Return True if this parser can read *path*."""

    @abstractmethod
    def has_workspace(self, path: Path) -> bool:
        """Return True if *path* contains workspace configuration."""

    def parse(self, path: Path) -> WorkspaceConfig:
        """Parse TOML from *path* and return a ``WorkspaceConfig``."""
        try:
            content = self.read_manifest_text(path)
        except WorkspaceParseError as exc:
            reason = redact_url_text(exc.reason)
            if reason == exc.reason:
                raise
            raise WorkspaceParseError(path, reason) from exc
        except Exception as exc:
            raise WorkspaceParseError(path, redact_url_text(str(exc))) from exc
        return self.parse_text(path, content)

    def parse_text(self, path: Path, content: str) -> WorkspaceConfig:
        """Parse *content* and bind the result to that manifest generation."""
        data = self.parse_toml_text_with_redacted_errors(content, path).unwrap()
        config = self.parse_data_with_redacted_errors(data, path)
        config._manifest_text = content
        return config

    def parse_data_with_redacted_errors(
        self,
        data: dict[str, Any],
        path: Path,
    ) -> WorkspaceConfig:
        """Parse manifest data without exposing credential-bearing diagnostics."""
        try:
            return self.parse_data(data, path)
        except WorkspaceParseError as exc:
            reason = redact_url_text(exc.reason)
            if reason == exc.reason:
                raise
            raise WorkspaceParseError(path, reason) from exc
        except Exception as exc:
            raise WorkspaceParseError(path, redact_url_text(str(exc))) from exc

    def validate_no_url_credentials(
        self,
        data: Mapping[str, Any],
        path: Path,
        *,
        content: str | None = None,
    ) -> None:
        """Reject sensitive URL material anywhere in manifest-owned data."""
        if has_url_credentials_in_data(data) or (
            content is not None and has_url_credentials(content)
        ):
            raise WorkspaceParseError(
                path,
                "embedded URL credentials are not supported",
            )

    @abstractmethod
    def parse_data(self, data: dict[str, Any], path: Path) -> WorkspaceConfig:
        """Parse already-loaded manifest *data* associated with *path*."""

    def has_tasks(self, path: Path) -> bool:
        """Return True if *path* contains task definitions."""
        return False

    def parse_tasks(self, path: Path) -> dict[str, Task]:
        """Parse *path* and return a mapping of task-name to Task."""
        try:
            data = self.load_toml(path).unwrap()
        except Exception as exc:
            raise TaskParseError(str(path), redact_url_text(str(exc))) from exc
        return self.parse_tasks_data(data)

    def parse_tasks_data(self, data: dict[str, Any]) -> dict[str, Task]:
        """Parse tasks from an already loaded manifest mapping."""
        return {}

    def add_task(self, path: Path, name: str, task: Task) -> None:
        """Persist a top-level task definition into *path*."""
        if path.exists():
            doc, generation = self.load_toml_with_generation(path)
        else:
            doc = tomlkit.document()
            generation = None

        tasks_section = doc.setdefault("tasks", tomlkit.table())
        tasks_section[name] = self.task_to_toml_inline(task)
        atomic_write_text(
            path,
            tomlkit.dumps(doc),
            expected_generation=generation,
        )

    def remove_task(self, path: Path, name: str) -> None:
        """Remove the top-level task named *name* from *path*."""
        doc, generation = self.load_toml_with_generation(path)
        tasks_section = doc.get("tasks", {})
        if name not in tasks_section:
            raise TaskNotFoundError(name, list(tasks_section.keys()))
        del tasks_section[name]
        self.remove_target_overrides(doc, name)
        atomic_write_text(
            path,
            tomlkit.dumps(doc),
            expected_generation=generation,
        )

    def task_to_toml_inline(self, task: Task) -> str | InlineTable:
        """Convert a *task* to a TOML-serializable value (string or inline table)."""
        table = tomlkit.inline_table()
        if task.cmd is not None:
            table.append("cmd", task.cmd)
        if task.depends_on:
            table.append("depends-on", [d.to_toml() for d in task.depends_on])
        if task.description:
            table.append("description", task.description)
        if task.env:
            table.append("env", dict(task.env))
        if task.cwd:
            table.append("cwd", task.cwd)
        if task.clean_env:
            table.append("clean-env", True)
        if task.default_environment:
            table.append("default-environment", task.default_environment)
        if task.args:
            table.append("args", [a.to_toml() for a in task.args])
        if task.inputs:
            table.append("inputs", list(task.inputs))
        if task.outputs:
            table.append("outputs", list(task.outputs))

        if len(table) == 1 and "cmd" in table:
            return str(table["cmd"])
        return table

    def remove_target_overrides(self, container: Container, name: str) -> None:
        """Remove *name* from every ``[target.<platform>.tasks]`` under *container*."""
        target = container.get("target")
        if not target:
            return
        for _platform, tdata in target.items():
            if tdata is None:
                continue
            tt = tdata.get("tasks")
            if tt is not None and name in tt:
                del tt[name]
