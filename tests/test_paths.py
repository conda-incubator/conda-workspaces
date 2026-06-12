"""Tests for conda_workspaces.paths."""

from __future__ import annotations

import pytest

from conda_workspaces.paths import (
    has_absolute_path_syntax,
    is_path_segment,
    parse_relative_posix_path,
)


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
        ("conda.toml", False, False, "conda.toml"),
        ("envs/default/history", False, False, "envs/default/history"),
        ("../target", True, False, "../target"),
        ("dir/./file", False, False, "dir/file"),
    ],
    ids=["file", "nested", "parent-allowed", "non-canonical-allowed"],
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
        ("C:project", True, False),
        ("dir/../file", False, False),
        ("dir/./file", False, True),
        ("dir//file", False, True),
        ("bad\0path", False, False),
    ],
    ids=[
        "empty",
        "posix-absolute",
        "windows-absolute-forward",
        "windows-absolute-backslash",
        "backslash",
        "windows-drive-relative",
        "parent",
        "current-dir-canonical",
        "double-slash-canonical",
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
