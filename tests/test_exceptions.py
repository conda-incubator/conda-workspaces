"""Tests for conda_workspaces.exceptions."""

from __future__ import annotations

from pathlib import Path

import pytest
from conda.exceptions import CondaError

from conda_workspaces.exceptions import (
    ActivationError,
    ArchiveError,
    ArchiveHashMismatchError,
    ArchivePathTraversalError,
    CondaWorkspacesError,
    EnvironmentNameInvalidError,
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
    FeatureNotFoundError,
    LockfileIntegrityError,
    LockfileNotFoundError,
    ManifestExistsError,
    PlatformError,
    SolveError,
    WorkspaceNotFoundError,
    WorkspaceParseError,
)


@pytest.mark.parametrize(
    "exc, expected_fragments",
    [
        (
            WorkspaceNotFoundError("/some/dir"),
            ["/some/dir"],
        ),
        (
            WorkspaceParseError("/path/pixi.toml", "bad syntax"),
            ["pixi.toml", "bad syntax"],
        ),
        (
            EnvironmentNotFoundError("dev", ["default", "test"]),
            ["dev", "default", "test"],
        ),
        (
            EnvironmentNotFoundError("dev", []),
            ["dev"],
        ),
        (
            EnvironmentNameInvalidError("../outside"),
            ["../outside", "not valid"],
        ),
        (
            FeatureNotFoundError("gpu", "train"),
            ["gpu", "train"],
        ),
        (
            PlatformError("win-arm64", ["linux-64", "osx-arm64"]),
            ["win-arm64"],
        ),
        (
            SolveError("test", "conflict"),
            ["test"],
        ),
        (
            ActivationError("dev", "shell not found"),
            ["dev", "shell not found"],
        ),
        (
            LockfileNotFoundError("test", Path("conda.lock")),
            ["test", "conda.lock"],
        ),
        (
            LockfileIntegrityError(Path("conda.lock"), "bad digest"),
            ["conda.lock", "bad digest"],
        ),
        (
            EnvironmentNotInstalledError("dev"),
            ["dev", "not installed"],
        ),
        (
            ManifestExistsError("pixi.toml"),
            ["pixi.toml", "already exists"],
        ),
    ],
    ids=[
        "workspace-not-found",
        "parse-error",
        "env-not-found-with-available",
        "env-not-found-empty",
        "invalid-env-name",
        "feature-not-found",
        "platform-error",
        "solve-error",
        "activation-error",
        "lockfile-not-found",
        "lockfile-integrity",
        "env-not-installed",
        "manifest-exists",
    ],
)
def test_exception_message(exc, expected_fragments):
    msg = str(exc)
    for fragment in expected_fragments:
        assert fragment in msg


def test_inheritance():
    assert issubclass(CondaWorkspacesError, CondaError)


@pytest.mark.parametrize(
    "exc, attr, expected",
    [
        (ActivationError("dev", "shell not found"), "environment", "dev"),
        (LockfileNotFoundError("test", Path("conda.lock")), "environment", "test"),
        (
            LockfileIntegrityError(Path("conda.lock"), "bad digest"),
            "reason",
            "bad digest",
        ),
    ],
    ids=["activation-env", "lockfile-env", "lockfile-integrity-reason"],
)
def test_exception_attributes(exc, attr, expected):
    assert getattr(exc, attr) == expected


@pytest.mark.parametrize(
    "exc",
    [
        WorkspaceNotFoundError("/some/dir"),
        WorkspaceParseError("/path/pixi.toml", "bad syntax"),
        EnvironmentNotFoundError("dev", ["default", "test"]),
        EnvironmentNameInvalidError("../outside"),
        EnvironmentNotInstalledError("dev"),
        ManifestExistsError("pixi.toml"),
        FeatureNotFoundError("gpu", "train"),
        PlatformError("win-arm64", ["linux-64", "osx-arm64"]),
        SolveError("test", "conflict"),
        ActivationError("dev", "shell not found"),
        LockfileNotFoundError("test", Path("conda.lock")),
        LockfileIntegrityError(Path("conda.lock"), "bad digest"),
    ],
    ids=[
        "workspace-not-found",
        "parse-error",
        "env-not-found",
        "invalid-env-name",
        "env-not-installed",
        "manifest-exists",
        "feature-not-found",
        "platform-error",
        "solve-error",
        "activation-error",
        "lockfile-not-found",
        "lockfile-integrity",
    ],
)
def test_error_message_and_hints_separate(exc):
    """error_message and hints are stored separately from str(exc)."""
    assert exc.error_message
    assert exc.error_message in str(exc)
    for hint in exc.hints:
        assert hint in str(exc)
    assert exc.error_message != str(exc) or not exc.hints


def test_archive_error():
    exc = ArchiveError("something broke")
    assert "something broke" in str(exc)
    assert exc.error_message == "something broke"


def test_archive_path_traversal_error():
    exc = ArchivePathTraversalError("../../etc/passwd")
    assert "../../etc/passwd" in str(exc)
    assert exc.hints


def test_archive_hash_mismatch_error():
    exc = ArchiveHashMismatchError("numpy-1.26.conda", expected="abc", actual="def")
    assert "numpy-1.26.conda" in str(exc)
    assert "abc" in str(exc)
    assert "def" in str(exc)
