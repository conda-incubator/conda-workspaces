"""Abstract base class for manifest parsers (workspaces and tasks)."""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import tomlkit

from ..exceptions import ManifestExistsError

if TYPE_CHECKING:
    from pathlib import Path
    from typing import ClassVar

    from tomlkit.container import Container
    from tomlkit.items import InlineTable

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
    def for_format(cls, alias: str) -> ManifestParser:
        """Return the registered parser whose :attr:`format_alias` matches *alias*.

        Raises :class:`ValueError` when no parser claims *alias*.  The
        registry is :data:`conda_workspaces.manifests._PARSERS`; this
        classmethod is the canonical way for CLI code to translate a
        ``--format`` value into the parser (and therefore the filename)
        it implies.
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
