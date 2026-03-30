"""Abstract base class for manifest parsers (workspaces and tasks)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import tomlkit

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
    handle via *filenames* and *extensions*.  The registry in
    ``parsers/__init__.py`` uses these to auto-detect the right parser.

    A single parser instance handles both workspace configuration and
    task definitions from the same file.
    """

    filenames: ClassVar[tuple[str, ...]] = ()

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
