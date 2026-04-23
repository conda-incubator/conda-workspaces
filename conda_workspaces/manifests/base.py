"""Abstract base class for manifest parsers (workspaces and tasks)."""

from __future__ import annotations

import re
import shutil
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import tomlkit
from packaging.requirements import InvalidRequirement, Requirement

from ..exceptions import ManifestExistsError

_PYPI_NAME_TAIL_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$")

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path
    from typing import Any, ClassVar

    from conda.models.environment import Environment
    from tomlkit.container import Container
    from tomlkit.items import InlineTable, Table

    from ..models import Task, WorkspaceConfig


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
        if target.exists():
            raise ManifestExistsError(target)
        shutil.copyfile(manifest, target)
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
        if path.exists():
            raise ManifestExistsError(path)

        doc = tomlkit.document()
        ws = tomlkit.table()
        ws.add("name", name)
        ws.add("channels", channels)
        ws.add("platforms", platforms)
        doc.add("workspace", ws)
        doc.add("dependencies", tomlkit.table())

        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
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

    def _emit_manifest(self, container: Table, data: dict[str, Any]) -> None:
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
        channels = list(envs[0].config.channels)

        # Per-platform specs as ``{name: suffix}`` dicts symmetric with
        # ``toml._parse_conda_deps`` / ``toml._parse_pypi_deps`` — a
        # name-only MatchSpec round-trips as ``"*"``, versioned
        # MatchSpecs keep their ``conda_build_form`` suffix, PyPI
        # entries keep their ``Requirement.specifier`` string.  When
        # a PyPI entry is not a valid PEP 508 string (e.g. the
        # ``"requests*"`` that
        # :meth:`~conda_workspaces.models.PyPIDependency.__str__`
        # emits for a ``requests = "*"`` manifest wildcard), fall
        # back to splitting name from specifier at the first
        # non-identifier character — matches what ``environment-yaml``
        # does in the same case: pass the input through as-is rather
        # than crashing.
        per_platform_conda: dict[str, dict[str, str]] = {}
        per_platform_pypi: dict[str, dict[str, str]] = {}
        for env in envs:
            conda_row: dict[str, str] = {}
            for spec in env.requested_packages:
                parts = spec.conda_build_form().split(None, 1)
                conda_row[parts[0]] = parts[1] if len(parts) > 1 else "*"
            per_platform_conda[env.platform] = conda_row

            pypi_row: dict[str, str] = {}
            for raw in env.external_packages.get("pip", []):
                try:
                    req = Requirement(raw)
                    pypi_row[req.name] = str(req.specifier) or "*"
                except InvalidRequirement:
                    match = _PYPI_NAME_TAIL_RE.match(raw.strip())
                    if match:
                        name_part, tail = match.groups()
                        pypi_row[name_part] = tail.strip() or "*"
            per_platform_pypi[env.platform] = pypi_row

        common_conda = cls._intersect_rows(per_platform_conda)
        common_pypi = cls._intersect_rows(per_platform_pypi)

        target: dict[str, dict[str, dict[str, str]]] = {}
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
    def _intersect_rows(cls, per_platform: dict[str, dict[str, str]]) -> dict[str, str]:
        """Return entries present in every platform mapping with identical values."""
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

    @abstractmethod
    def parse(self, path: Path) -> WorkspaceConfig:
        """Parse *path* and return a ``WorkspaceConfig``."""

    def has_tasks(self, path: Path) -> bool:
        """Return True if *path* contains task definitions."""
        return False

    def parse_tasks(self, path: Path) -> dict[str, Task]:
        """Parse *path* and return a mapping of task-name to Task."""
        return {}

    def add_task(self, path: Path, name: str, task: Task) -> None:
        """Persist a new task definition into *path*."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support writing tasks to {path.name}."
        )

    def remove_task(self, path: Path, name: str) -> None:
        """Remove the task named *name* from *path*."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support writing tasks to {path.name}."
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
