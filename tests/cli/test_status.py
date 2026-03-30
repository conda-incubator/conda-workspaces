"""Tests for conda_workspaces.cli.status."""

from __future__ import annotations

import pytest
from conda.exceptions import CondaError
from rich.console import Console

from conda_workspaces.cli.status import _class_name_to_label, print_error
from conda_workspaces.exceptions import (
    EnvironmentNotFoundError,
    SolveError,
)


@pytest.mark.parametrize(
    "class_name, expected",
    [
        ("ArgumentError", "argument"),
        ("AuthenticationError", "authentication"),
        ("BasicClobberError", "basic clobber"),
        ("BinaryPrefixReplacementError", "binary prefix replacement"),
        ("ChannelError", "channel"),
        ("ChecksumMismatchError", "checksum mismatch"),
        ("ClobberError", "clobber"),
        ("CommandNotFoundError", "command not found"),
        ("CondaDependencyError", "conda dependency"),
        ("CondaEnvironmentError", "conda environment"),
        ("CondaError", "conda"),
        ("CondaFileIOError", "conda file IO"),
        ("CondaHTTPError", "conda HTTP"),
        ("CondaHistoryError", "conda history"),
        ("CondaIOError", "conda IO"),
        ("CondaImportError", "conda import"),
        ("CondaIndexError", "conda index"),
        ("CondaKeyError", "conda key"),
        ("CondaMemoryError", "conda memory"),
        ("CondaMultiError", "conda multi"),
        ("CondaOSError", "conda OS"),
        ("CondaSSLError", "conda SSL"),
        ("CondaUpdatePackageError", "conda update package"),
        ("CondaUpgradeError", "conda upgrade"),
        ("CondaValueError", "conda value"),
        ("CondaVerificationError", "conda verification"),
        ("CorruptedEnvironmentError", "corrupted environment"),
        ("CouldntParseError", "couldnt parse"),
        ("CyclicalDependencyError", "cyclical dependency"),
        ("DeprecatedError", "deprecated"),
        ("DirectoryNotACondaEnvironmentError", "directory not a conda environment"),
        ("DirectoryNotFoundError", "directory not found"),
        ("DisallowedPackageError", "disallowed package"),
        ("EncodingError", "encoding"),
        ("EnvironmentFileTypeMismatchError", "environment file type mismatch"),
        ("EnvironmentIsFrozenError", "environment is frozen"),
        ("EnvironmentNotWritableError", "environment not writable"),
        ("JSONDecodeError", "JSON decode"),
        ("KnownPackageClobberError", "known package clobber"),
        ("LinkError", "link"),
        ("LockError", "lock"),
        ("NoBaseEnvironmentError", "no base environment"),
        ("NoChannelsConfiguredError", "no channels configured"),
        ("NoPackagesFoundError", "no packages found"),
        ("NoSpaceLeftError", "no space left"),
        ("NoWritableEnvsDirError", "no writable envs dir"),
        ("NoWritablePkgsDirError", "no writable pkgs dir"),
        ("NotWritableError", "not writable"),
        ("OfflineError", "offline"),
        ("PackageNotInstalledError", "package not installed"),
        ("PackagesNotFoundError", "packages not found"),
        ("PaddingError", "padding"),
        ("ParseError", "parse"),
        ("PathNotFoundError", "path not found"),
        ("PluginError", "plugin"),
        ("ProxyError", "proxy"),
        ("RemoveError", "remove"),
        ("SafetyError", "safety"),
        ("SharedLinkPathClobberError", "shared link path clobber"),
        ("SpecsConfigurationConflictError", "specs configuration conflict"),
        ("TooManyArgumentsError", "too many arguments"),
        ("UnknownPackageClobberError", "unknown package clobber"),
        ("UnsatisfiableError", "unsatisfiable"),
    ],
)
def test_class_name_to_label(class_name, expected):
    assert _class_name_to_label(class_name) == expected


@pytest.mark.parametrize(
    "exc, expected_error, expected_hint",
    [
        (
            EnvironmentNotFoundError("dev", ["default", "test"]),
            "dev", "default",
        ),
        (
            SolveError("test", "conflict"),
            "conflict", "dependency specifications",
        ),
    ],
    ids=["env-not-found", "solve-error"],
)
def test_print_error_with_hints(capsys, exc, expected_error, expected_hint):
    console = Console(stderr=True, highlight=False, force_terminal=False)
    print_error(console, exc)
    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert expected_error in captured.err
    assert "Hint:" in captured.err
    assert expected_hint in captured.err


def test_print_error_conda_exception(capsys):
    exc = CondaError("something went wrong")
    console = Console(stderr=True, highlight=False, force_terminal=False)
    print_error(console, exc)
    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert "conda:" in captured.err
    assert "something went wrong" in captured.err
    assert "Hint:" not in captured.err


def test_print_error_multi_error_dedup(capsys):
    class FakeMulti(Exception):
        pass

    inner = CondaError("broken")
    multi = FakeMulti()
    multi.errors = [inner, inner]

    console = Console(stderr=True, highlight=False, force_terminal=False)
    print_error(console, multi)
    captured = capsys.readouterr()
    assert captured.err.count("Error:") == 1


@pytest.mark.parametrize(
    "verb, noun, name, kwargs, expected",
    [
        ("Running", "task", "lint", {}, ["Running", "lint", "task"]),
        (
            "Installing", "environment", "default",
            {"style": "bold blue"},
            ["[bold blue]Installing[/bold blue]", "default", "environment"],
        ),
        ("Running", "task", "lint", {"ellipsis": True}, ["..."]),
        (
            "Running", "task", "lint",
            {"detail": "echo hello"},
            ["echo hello"],
        ),
        (
            "Skipped", "task", "lint",
            {"suffix": "cached"},
            ["(cached)"],
        ),
    ],
    ids=["basic", "style", "ellipsis", "detail", "suffix"],
)
def test_format(verb, noun, name, kwargs, expected):
    from conda_workspaces.cli.status import _format

    result = _format(verb, noun, name, **kwargs)
    for substring in expected:
        assert substring in result


def test_format_verb_name_noun_order():
    from conda_workspaces.cli.status import _format

    result = _format("Installed", "environment", "default")
    idx_verb = result.find("Installed")
    idx_name = result.find("default")
    idx_noun = result.find("environment")
    assert idx_verb < idx_name < idx_noun


def test_message_prints_to_console():
    from io import StringIO

    from rich.console import Console as RichConsole

    from conda_workspaces.cli import status

    buf = StringIO()
    console = RichConsole(file=buf, highlight=False, force_terminal=False)
    status.message(console, "Installed", "environment", "default")
    output = buf.getvalue()
    assert "Installed" in output
    assert "default" in output
    assert "environment" in output


def test_message_default_style_applied():
    from io import StringIO

    from rich.console import Console as RichConsole

    from conda_workspaces.cli import status

    buf = StringIO()
    console = RichConsole(
        file=buf, highlight=False,
        force_terminal=True, color_system="truecolor",
    )
    status.message(console, "Installed", "environment", "default")
    output = buf.getvalue()
    assert "\x1b[" in output
    assert "Installed" in output
