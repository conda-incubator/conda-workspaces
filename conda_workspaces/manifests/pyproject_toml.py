"""Parser for pyproject.toml workspace manifests.

Reads workspace configuration from ``pyproject.toml``, trying these
tables in order:

1. ``[tool.conda.workspace]``  – conda-native table
2. ``[tool.pixi.workspace]``   – pixi compatibility
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit

from ..exceptions import (
    ManifestExistsError,
    TaskNotFoundError,
    TaskParseError,
    WorkspaceParseError,
)
from ..models import WorkspaceConfig
from .base import ManifestParser
from .normalize import parse_feature_tasks, parse_tasks_and_targets
from .toml import _parse_channels, _parse_features_and_envs

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from conda.models.environment import Environment
    from tomlkit.items import Table

    from ..models import Task


class PyprojectTomlParser(ManifestParser):
    """Parse workspace and task config from ``pyproject.toml``.

    Tries these tool tables in priority order:

    1. ``[tool.conda.*]`` – conda-native tables
    2. ``[tool.pixi.*]`` – pixi compatibility
    """

    format_alias = "pyproject"
    filenames = ("pyproject.toml",)
    exporter_format = "pyproject-toml"

    def can_handle(self, path: Path) -> bool:
        return path.name in self.filenames

    def write_workspace_stub(
        self,
        base_dir: Path,
        name: str,
        channels: list[str],
        platforms: list[str],
    ) -> tuple[Path, str]:
        """Add ``[tool.conda.workspace]`` to *base_dir*/``pyproject.toml``.

        Unlike the default :meth:`ManifestParser.write_workspace_stub`
        (which refuses to touch an existing file),
        ``pyproject.toml`` is a shared packaging manifest owned by the
        Python ecosystem — PEP 621 ``[project]``, ``[build-system]``,
        and other tooling tables routinely coexist with ours.  We read
        the existing document if any, add our configuration under the
        nested ``[tool.conda]`` table, and report ``"Updated"`` so the
        CLI can distinguish an append from a create.  An existing
        ``[tool.conda]`` or ``[tool.pixi]`` raises
        :class:`ManifestExistsError`.
        """
        path = self.manifest_path(base_dir)
        existed = path.exists()
        if existed:
            doc = tomlkit.loads(path.read_text(encoding="utf-8"))
        else:
            doc = tomlkit.document()

        tool = doc.setdefault("tool", tomlkit.table())
        if "conda" in tool:
            raise ManifestExistsError("[tool.conda] in pyproject.toml")
        if "pixi" in tool:
            raise ManifestExistsError("[tool.pixi] in pyproject.toml")

        conda = tomlkit.table()
        ws = tomlkit.table()
        ws.add("name", name)
        ws.add("channels", channels)
        ws.add("platforms", platforms)
        conda.add("workspace", ws)
        conda.add("dependencies", tomlkit.table())
        tool.add("conda", conda)

        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        return path, "Updated" if existed else "Created"

    def export(self, envs: Iterable[Environment]) -> str:
        """Serialize *envs* as a ``pyproject.toml`` with ``[tool.conda.*]``.

        Same content as :meth:`ManifestParser.export` — workspace
        table, dependencies, optional pypi-dependencies, optional
        per-platform overrides — but wrapped under ``[tool.conda]``
        so the output drops straight into PEP 621 / ``pyproject.toml``
        alongside ``[project]``, ``[build-system]``, and peer tables.
        """
        doc = tomlkit.document()
        tool = tomlkit.table(is_super_table=True)
        conda = tomlkit.table()
        self._emit_manifest(conda, self.manifest_data(envs))
        tool.add("conda", conda)
        doc.add("tool", tool)
        return tomlkit.dumps(doc)

    def merge_export(self, existing_path: Path, exported: str) -> str:
        """Splice *exported*'s ``[tool.conda]`` into *existing_path*.

        ``pyproject.toml`` is a shared packaging manifest owned by
        the Python ecosystem; the default "overwrite the file
        wholesale" behaviour of :meth:`ManifestParser.merge_export`
        would silently destroy ``[project]`` / ``[build-system]`` /
        ``[tool.ruff]`` / etc.  Instead we parse the existing
        document, replace its ``[tool.conda]`` subtree with the one
        :meth:`export` just produced, and serialise the result.

        This is the export-side companion to
        :meth:`write_workspace_stub`, which does the same kind of
        nested-table merge for ``conda workspace init``.  Existing
        ``[tool.pixi]`` content is preserved untouched — users who
        mix both tools stay functional.
        """
        exported_doc = tomlkit.loads(exported)
        exported_conda = exported_doc.get("tool", {}).get("conda")
        if exported_conda is None:
            return exported

        doc = tomlkit.loads(existing_path.read_text(encoding="utf-8"))
        tool = doc.setdefault("tool", tomlkit.table())
        if "conda" in tool:
            del tool["conda"]
        tool["conda"] = exported_conda
        return tomlkit.dumps(doc)

    def has_workspace(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        tool = data.get("tool", {})
        conda = tool.get("conda", {})
        pixi = tool.get("pixi", {})
        return bool(conda.get("workspace") or pixi.get("workspace"))

    def parse(self, path: Path) -> WorkspaceConfig:
        try:
            text = path.read_text(encoding="utf-8")
            # ``unwrap()`` collapses tomlkit subclasses to native Python
            # types so downstream code (resolver, exporter, YAML writer)
            # never has to defend against ``tomlkit.items.String`` etc.
            data = tomlkit.loads(text).unwrap()
        except Exception as exc:
            raise WorkspaceParseError(path, str(exc)) from exc

        tool = data.get("tool", {})
        root = str(path.parent)

        conda = tool.get("conda", {})
        pixi = tool.get("pixi", {})

        if conda.get("workspace"):
            source = conda
        elif pixi.get("workspace"):
            source = pixi
        else:
            raise WorkspaceParseError(
                path,
                "No [tool.conda.workspace] or [tool.pixi.workspace] table found",
            )

        ws = source.get("workspace", {})

        config = WorkspaceConfig(
            name=ws.get("name") or data.get("project", {}).get("name"),
            version=ws.get("version") or data.get("project", {}).get("version"),
            description=data.get("project", {}).get("description"),
            channels=_parse_channels(ws.get("channels", [])),
            platforms=list(ws.get("platforms", [])),
            root=root,
            manifest_path=str(path),
            channel_priority=ws.get("channel-priority"),
        )

        _parse_features_and_envs(source, config, path)
        return config

    def has_tasks(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8")).unwrap()
        except Exception:
            return False
        tool = data.get("tool", {})
        return bool(
            tool.get("conda", {}).get("tasks") or tool.get("pixi", {}).get("tasks")
        )

    def parse_tasks(self, path: Path) -> dict[str, Task]:
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8")).unwrap()
        except Exception as exc:
            raise TaskParseError(str(path), str(exc)) from exc

        tool = data.get("tool", {})
        source = tool.get("conda", {}) or tool.get("pixi", {})

        tasks = parse_tasks_and_targets(source)
        parse_feature_tasks(source, tasks)
        return tasks

    def tool_section_for_tasks(self, doc: tomlkit.TOMLDocument) -> Table:
        """Return the ``tool`` sub-table that owns tasks.

        Uses the same precedence as ``parse_tasks``: non-empty
        ``tool.conda`` wins, then non-empty ``tool.pixi``, then
        falls back to ``tool.conda`` for new manifests.
        """
        tool = doc.setdefault("tool", tomlkit.table())
        conda = tool.get("conda")
        pixi = tool.get("pixi")
        if conda is not None and len(conda) > 0:
            return tool.setdefault("conda", tomlkit.table())
        if pixi is not None and len(pixi) > 0:
            return tool.setdefault("pixi", tomlkit.table())
        return tool.setdefault("conda", tomlkit.table())

    def add_task(self, path: Path, name: str, task: Task) -> None:
        if path.exists():
            doc = tomlkit.loads(path.read_text(encoding="utf-8"))
        else:
            doc = tomlkit.document()

        parent = self.tool_section_for_tasks(doc)
        tasks_section = parent.setdefault("tasks", tomlkit.table())
        tasks_section[name] = self.task_to_toml_inline(task)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    def remove_task(self, path: Path, name: str) -> None:
        doc = tomlkit.loads(path.read_text(encoding="utf-8"))
        tool = doc.get("tool", {})
        available: list[str] = []
        for sec_name in ("conda", "pixi"):
            sec = tool.get(sec_name)
            if sec is None:
                continue
            tasks_tbl = sec.get("tasks")
            if tasks_tbl is None:
                continue
            available.extend(tasks_tbl.keys())
            if name in tasks_tbl:
                del tasks_tbl[name]
                self.remove_target_overrides(sec, name)
                path.write_text(tomlkit.dumps(doc), encoding="utf-8")
                return
        raise TaskNotFoundError(name, sorted(set(available)))
