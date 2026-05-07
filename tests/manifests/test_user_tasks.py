"""Tests for user-level task file discovery and merge semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from conda_workspaces.exceptions import NoTaskFileError
from conda_workspaces.manifests import (
    cached_task_parse,
    cached_user_task_parse,
    detect_and_parse_tasks,
    user_task_file,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear LRU caches between tests."""
    cached_task_parse.cache_clear()
    cached_user_task_parse.cache_clear()


USER_TASKS_TOML = """\
[tasks]
greet = "echo hello"
lint = "ruff check --global"
"""

PROJECT_TASKS_TOML = """\
[tasks]
build = "make build"
lint = "ruff check ."
"""


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a fake home directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


@pytest.mark.parametrize(
    ("env_xdg", "rel_path"),
    [
        ("custom_xdg", Path("custom_xdg") / "conda" / "tasks.toml"),
        (None, Path(".config") / "conda" / "tasks.toml"),
        (None, Path(".conda") / "tasks.toml"),
    ],
    ids=["xdg-override", "default-config", "legacy-fallback"],
)
def test_user_task_file_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
    env_xdg: str | None,
    rel_path: Path,
):
    if env_xdg is not None:
        xdg_dir = tmp_path / env_xdg
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
        task_file = xdg_dir / "conda" / "tasks.toml"
    else:
        task_file = fake_home / rel_path

    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(USER_TASKS_TOML)

    result = user_task_file()
    assert result == task_file


def test_user_task_file_xdg_takes_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
):
    """XDG_CONFIG_HOME wins over ~/.config and ~/.conda."""
    xdg_dir = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))

    xdg_file = xdg_dir / "conda" / "tasks.toml"
    xdg_file.parent.mkdir(parents=True)
    xdg_file.write_text(USER_TASKS_TOML)

    default_file = fake_home / ".config" / "conda" / "tasks.toml"
    default_file.parent.mkdir(parents=True)
    default_file.write_text(USER_TASKS_TOML)

    assert user_task_file() == xdg_file


def test_user_task_file_none_when_missing(fake_home: Path):
    assert user_task_file() is None


@pytest.mark.parametrize(
    "use_file_path",
    [False, True],
    ids=["via-start-dir", "via-file-path"],
)
def test_merge_project_overrides_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
    use_file_path: bool,
):
    """Project tasks win on name collision; user-only tasks survive."""
    user_file = fake_home / ".config" / "conda" / "tasks.toml"
    user_file.parent.mkdir(parents=True)
    user_file.write_text(USER_TASKS_TOML)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "conda.toml"
    project_file.write_text(PROJECT_TASKS_TOML)
    monkeypatch.chdir(project_dir)

    if use_file_path:
        path, tasks, user_only = detect_and_parse_tasks(
            file_path=project_file,
        )
        assert path == project_file.resolve()
    else:
        path, tasks, user_only = detect_and_parse_tasks(
            start_dir=project_dir,
        )
        assert path == project_file

    assert "build" in tasks
    assert "greet" in tasks
    assert "lint" in tasks
    assert tasks["lint"].cmd == "ruff check ."
    assert user_only == {"greet"}


def test_user_tasks_only_when_no_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
):
    """When no project file exists, user tasks are the sole source."""
    user_file = fake_home / ".config" / "conda" / "tasks.toml"
    user_file.parent.mkdir(parents=True)
    user_file.write_text(USER_TASKS_TOML)

    project_dir = tmp_path / "empty_project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)

    path, tasks, user_only = detect_and_parse_tasks(
        start_dir=project_dir,
    )

    assert path == user_file
    assert set(tasks) == {"greet", "lint"}
    assert user_only == {"greet", "lint"}


def test_no_files_raises_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
):
    project_dir = tmp_path / "empty"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)

    with pytest.raises(NoTaskFileError):
        detect_and_parse_tasks(start_dir=project_dir)


