"""Parser for pixi.toml workspace manifests.

Reads ``[workspace]`` (or ``[project]`` for legacy manifests),
``[dependencies]``, ``[pypi-dependencies]``, ``[feature.*]``,
``[environments]``, and ``[target.*]`` tables from pixi.toml.
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

    from ..models import Task


class PixiTomlParser(ManifestParser):
    """Parse ``pixi.toml`` manifests (workspace and tasks)."""

    filenames = ("pixi.toml",)

    def can_handle(self, path: Path) -> bool:
        return path.name in self.filenames

    def has_workspace(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return "workspace" in data or "project" in data

    def parse(self, path: Path) -> WorkspaceConfig:
        try:
            text = path.read_text(encoding="utf-8")
            data = tomlkit.loads(text)
        except Exception as exc:
            raise WorkspaceParseError(path, str(exc)) from exc

        root = str(path.parent)

        # workspace table (pixi v0.23+) or legacy project table
        ws = data.get("workspace", data.get("project", {}))
        if not ws:
            raise WorkspaceParseError(path, "No [workspace] or [project] table found")

        config = WorkspaceConfig(
            name=ws.get("name"),
            version=ws.get("version"),
            description=ws.get("description"),
            channels=_parse_channels(ws.get("channels", [])),
            platforms=list(ws.get("platforms", [])),
            root=root,
            manifest_path=str(path),
            channel_priority=ws.get("channel-priority"),
        )

        _parse_features_and_envs(data, config, path)
        return config

    def has_tasks(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return bool(data.get("tasks"))

    def parse_tasks(self, path: Path) -> dict[str, Task]:
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8")).unwrap()
        except Exception as exc:
            raise TaskParseError(str(path), str(exc)) from exc

        tasks = parse_tasks_and_targets(data)
        parse_feature_tasks(data, tasks)
        return tasks

    def add_task(self, path: Path, name: str, task: Task) -> None:
        if path.exists():
            doc = tomlkit.loads(path.read_text(encoding="utf-8"))
        else:
            doc = tomlkit.document()

        tasks_section = doc.setdefault("tasks", tomlkit.table())
        tasks_section[name] = self.task_to_toml_inline(task)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    def remove_task(self, path: Path, name: str) -> None:
        doc = tomlkit.loads(path.read_text(encoding="utf-8"))
        tasks_section = doc.get("tasks", {})
        if name not in tasks_section:
            raise TaskNotFoundError(name, list(tasks_section.keys()))
        del tasks_section[name]
        self.remove_target_overrides(doc, name)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
