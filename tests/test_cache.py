"""Tests for conda_workspaces.cache."""

from __future__ import annotations

import pytest

from conda_workspaces.cache import (
    _expand_globs,
    _file_sha256,
    _file_stat,
    _files_match,
    _fingerprint_files,
    is_cached,
    save_cache,
)


def test_file_stat(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    stat = _file_stat(str(f))
    assert stat is not None
    mtime, size = stat
    assert size == 5
    assert mtime > 0


def test_file_stat_missing():
    assert _file_stat("/nonexistent/file") is None


def test_file_sha256(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    h = _file_sha256(str(f))
    assert isinstance(h, str)
    assert len(h) == 64


def test_file_sha256_deterministic(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    assert _file_sha256(str(f)) == _file_sha256(str(f))


@pytest.mark.parametrize(
    ("pattern", "setup", "expected_count"),
    [
        (
            ["*.py"],
            {"a.py": "", "b.py": "", "c.txt": ""},
            2,
        ),
        (
            ["**/*.py"],
            {"b.py": "", "sub/a.py": ""},
            2,
        ),
    ],
    ids=["flat", "recursive"],
)
def test_expand_globs(tmp_path, pattern, setup, expected_count):
    for name, content in setup.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    result = _expand_globs(pattern, tmp_path)
    assert len(result) == expected_count


@pytest.mark.parametrize(
    ("paths", "expected_keys"),
    [
        (["file.txt"], {"mtime", "size", "sha256"}),
    ],
)
def test_fingerprint_files(tmp_path, paths, expected_keys):
    for name in paths:
        (tmp_path / name).write_text("content")
    fp = _fingerprint_files([str(tmp_path / n) for n in paths])
    for name in paths:
        entry = fp[str(tmp_path / name)]
        assert set(entry.keys()) == expected_keys


def test_fingerprint_missing_file():
    fp = _fingerprint_files(["/nonexistent/path"])
    assert len(fp) == 0


@pytest.mark.parametrize(
    ("cached", "current", "expected"),
    [
        pytest.param(
            {"main.py": {"mtime": 1.0, "size": 2, "sha256": "old"}},
            {"main.py": {"mtime": 1.0, "size": 2, "sha256": "new"}},
            False,
            id="same-stat-different-digest",
        ),
        pytest.param(
            {"main.py": {"mtime": 1.0, "size": 2, "sha256": "same"}},
            {"main.py": {"mtime": 2.0, "size": 2, "sha256": "same"}},
            True,
            id="same-digest-different-mtime",
        ),
        pytest.param(
            {"main.py": {"mtime": 1.0, "size": 2}},
            {"main.py": {"mtime": 1.0, "size": 2}},
            True,
            id="legacy-same-stat",
        ),
        pytest.param(
            {"main.py": {"mtime": 1.0, "size": 2}},
            {"main.py": {"mtime": 2.0, "size": 2}},
            False,
            id="legacy-mtime-change",
        ),
    ],
)
def test_files_match_compares_digests_when_available(cached, current, expected):
    assert _files_match(cached, current) is expected


def test_not_cached_initially(tmp_path):
    assert not is_cached(
        tmp_path,
        "build",
        "make",
        {},
        ["*.py"],
        ["dist/"],
        tmp_path,
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "make",
        ["python", "-c", "print(1); echo ARRAY_PWN"],
    ],
    ids=["shell-string", "argv-list"],
)
def test_save_and_check(tmp_path, cmd):
    src = tmp_path / "main.py"
    src.write_text("print('hello')")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "app.whl").write_text("fake wheel")

    save_cache(
        tmp_path,
        "build",
        cmd,
        {},
        ["main.py"],
        ["dist/*.whl"],
        tmp_path,
    )
    assert is_cached(
        tmp_path,
        "build",
        cmd,
        {},
        ["main.py"],
        ["dist/*.whl"],
        tmp_path,
    )


@pytest.mark.parametrize(
    ("save_cmd", "save_env", "check_cmd", "check_env", "mutate"),
    [
        ("make", {}, "make", {}, "input"),
        ("make", {}, "make all", {}, None),
        (
            ["python", "-m", "pytest"],
            {},
            ["python", "-m", "pytest", "tests/unit"],
            {},
            None,
        ),
        ("make", {"CC": "gcc"}, "make", {"CC": "clang"}, None),
        ("make", {}, "make", {}, "delete_output"),
    ],
    ids=[
        "input-change",
        "cmd-change",
        "argv-cmd-change",
        "env-change",
        "missing-output",
    ],
)
def test_cache_invalidation(
    tmp_path,
    save_cmd,
    save_env,
    check_cmd,
    check_env,
    mutate,
):
    src = tmp_path / "main.py"
    src.write_text("v1")

    outputs = []
    if mutate == "delete_output":
        out = tmp_path / "output.txt"
        out.write_text("result")
        outputs = ["output.txt"]

    save_cache(
        tmp_path,
        "build",
        save_cmd,
        save_env,
        ["main.py"],
        outputs,
        tmp_path,
    )

    if mutate == "input":
        src.write_text("v2")
    elif mutate == "delete_output":
        (tmp_path / "output.txt").unlink()

    assert not is_cached(
        tmp_path,
        "build",
        check_cmd,
        check_env,
        ["main.py"],
        outputs,
        tmp_path,
    )
