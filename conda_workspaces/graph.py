"""DAG resolution and topological sort for task dependencies."""

from __future__ import annotations

from collections import deque
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING

from .exceptions import CyclicDependencyError, TaskNotFoundError

if TYPE_CHECKING:
    from .models import Task


def resolve_execution_order(
    target: str,
    tasks: dict[str, Task],
    *,
    skip_deps: bool = False,
) -> list[str]:
    """Return task names in the order they should execute to run *target*.

    Uses :class:`graphlib.TopologicalSorter`. Only the transitive
    closure of *target*'s dependencies is included -- unrelated tasks
    are omitted.

    Raises ``TaskNotFoundError`` if *target* or any dependency is missing.
    Raises ``CyclicDependencyError`` if the dependency graph has a cycle.
    """
    if target not in tasks:
        raise TaskNotFoundError(target, list(tasks.keys()))

    if skip_deps:
        return [target]

    reachable = _collect_reachable(target, tasks)
    return _topological_sort(reachable, tasks)


def _collect_reachable(target: str, tasks: dict[str, Task]) -> set[str]:
    """BFS to gather all tasks reachable via depends-on from *target*."""
    visited: set[str] = set()
    queue = deque([target])
    while queue:
        name = queue.popleft()
        if name in visited:
            continue
        if name not in tasks:
            raise TaskNotFoundError(name, list(tasks.keys()))
        visited.add(name)
        for dep in tasks[name].depends_on:
            if dep.task not in visited:
                queue.append(dep.task)
    return visited


def _topological_sort(names: set[str], tasks: dict[str, Task]) -> list[str]:
    """Topologically sort over the subset *names*."""
    graph = {
        name: sorted(dep.task for dep in tasks[name].depends_on if dep.task in names)
        for name in sorted(names)
    }
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        cycle = list(exc.args[1]) if len(exc.args) > 1 else sorted(names)
        raise CyclicDependencyError(cycle) from exc
