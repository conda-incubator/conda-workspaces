"""Parser for pyproject.toml workspace manifests.

Reads workspace configuration from ``pyproject.toml``, trying these
tables in order:

1. ``[tool.conda.workspace]``  – conda-native table
2. ``[tool.pixi.workspace]``   – pixi compatibility
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit

from ..exceptions import TaskNotFoundError, TaskParseError, WorkspaceParseError
from ..models import WorkspaceConfig
from .base import ManifestParser
from .normalize import parse_feature_tasks, parse_tasks_and_targets
from .toml import _parse_channels, _parse_features_and_envs

if TYPE_CHECKING:
    from pathlib import Path

    from tomlkit.items import Table

    from ..models import Task


class PyprojectTomlParser(ManifestParser):
    """Parse workspace and task config from ``pyproject.toml``.

    Tries these tool tables in priority order:

    1. ``[tool.conda.*]`` – conda-native tables
    2. ``[tool.pixi.*]`` – pixi compatibility
    """

    filenames = ("pyproject.toml",)

    def can_handle(self, path: Path) -> bool:
        return path.name in self.filenames

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
            data = tomlkit.loads(text)
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
