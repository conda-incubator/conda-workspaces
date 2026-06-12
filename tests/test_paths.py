"""Tests for conda_workspaces.paths."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.paths import (
    has_absolute_path_syntax,
    is_path_segment,
    parse_relative_posix_path,
    resolve_relative_path,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/tmp/project", True),
        ("C:/project", True),
        (r"C:\project", True),
        ("relative/project", False),
        (r"relative\project", False),
        ("C:project", False),
    ],
    ids=[
        "posix-absolute",
        "windows-absolute-forward",
        "windows-absolute-backslash",
        "relative-posix",
        "relative-windows",
        "windows-drive-relative",
    ],
)
def test_has_absolute_path_syntax(value: str, expected: bool) -> None:
    assert has_absolute_path_syntax(value) is expected


@pytest.mark.parametrize(
    ("value", "allow_parent", "require_canonical", "expected"),
    [
        ("environment.yml", False, False, "environment.yml"),
        ("envs/default.yml", False, False, "envs/default.yml"),
        ("./envs/default.yml", False, False, "envs/default.yml"),
        ("envs//default.yml", False, False, "envs/default.yml"),
        ("envs/./default.yml", False, False, "envs/default.yml"),
        ("../target", True, False, "../target"),
    ],
    ids=[
        "file",
        "nested",
        "current-dir-prefix",
        "double-separator",
        "current-dir-segment",
        "allowed-parent",
    ],
)
def test_parse_relative_posix_path_accepts_valid_paths(
    value: str,
    allow_parent: bool,
    require_canonical: bool,
    expected: str,
) -> None:
    path = parse_relative_posix_path(
        value,
        allow_parent=allow_parent,
        require_canonical=require_canonical,
    )

    assert path.as_posix() == expected


@pytest.mark.parametrize(
    ("value", "allow_parent", "require_canonical"),
    [
        ("", False, False),
        ("/tmp/project", False, False),
        ("C:/project", False, False),
        (r"C:\project", False, False),
        (r"dir\file", False, False),
        (r"\file", False, False),
        (r"\\server\share\file", False, False),
        ("C:project", True, False),
        ("../file", False, False),
        ("dir/../file", False, False),
        ("./envs/default.yml", False, True),
        ("envs//default.yml", False, True),
        ("envs/./default.yml", False, True),
        ("bad\0path", False, False),
    ],
    ids=[
        "empty",
        "posix-absolute",
        "windows-absolute-forward",
        "windows-absolute-backslash",
        "backslash",
        "windows-rooted",
        "windows-unc",
        "windows-drive-relative",
        "parent",
        "nested-parent",
        "current-dir-canonical",
        "double-slash-canonical",
        "current-dir-segment-canonical",
        "nul",
    ],
)
def test_parse_relative_posix_path_rejects_invalid_paths(
    value: str,
    allow_parent: bool,
    require_canonical: bool,
) -> None:
    with pytest.raises(ValueError):
        parse_relative_posix_path(
            value,
            allow_parent=allow_parent,
            require_canonical=require_canonical,
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("archive-test", True),
        ("archive test", True),
        ("", False),
        (".", False),
        ("..", False),
        ("nested/archive-test", False),
        (r"nested\archive-test", False),
        ("/tmp/archive-test", False),
        (r"\archive-test", False),
        ("C:archive-test", False),
        ("C:/archive-test", False),
        (r"C:\archive-test", False),
        (r"\\server\share\archive-test", False),
        ("bad\0name", False),
    ],
    ids=[
        "name",
        "space",
        "empty",
        "dot",
        "dot-dot",
        "nested-posix",
        "nested-windows",
        "absolute",
        "windows-rooted",
        "windows-drive-relative",
        "windows-absolute-forward",
        "windows-absolute-backslash",
        "windows-unc",
        "nul",
    ],
)
def test_is_path_segment(value: str, expected: bool) -> None:
    assert is_path_segment(value) is expected


@pytest.mark.parametrize(
    "relative_path",
    [
        PurePosixPath("environment.yml"),
        PurePosixPath("envs/default.yml"),
    ],
    ids=["file", "nested"],
)
def test_resolve_relative_path_returns_paths_inside_root(
    tmp_path: Path,
    relative_path: PurePosixPath,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    assert resolve_relative_path(root, relative_path) == root.joinpath(
        *relative_path.parts
    ).resolve(strict=False)


def test_resolve_relative_path_rejects_symlink_escape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    try:
        (project / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(ValueError):
        resolve_relative_path(project, PurePosixPath("linked/environment.yml"))
