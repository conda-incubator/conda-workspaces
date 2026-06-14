"""Tests for conda_workspaces.template."""

from __future__ import annotations

import pytest
from conda.base.constants import on_win
from conda.base.context import context
from conda.utils import quote_for_shell

import conda_workspaces.template as template_mod
from conda_workspaces.template import render, render_command, render_list


def test_render_no_template_fast_path():
    assert render("echo hello") == "echo hello"


def test_render_simple_variable():
    result = render("echo {{ greeting }}", task_args={"greeting": "hi"})
    assert result == "echo hi"


@pytest.mark.parametrize(
    ("template", "task_args", "expected"),
    [
        (
            "python -m pytest {{ target }}",
            {"target": "tests/unit; echo ARG_PWN"},
            f"python -m pytest {quote_for_shell('tests/unit; echo ARG_PWN')}",
        ),
        (
            "echo {{ name }}",
            {"name": "simple"},
            "echo simple",
        ),
    ],
    ids=["metacharacters", "simple"],
)
def test_render_command_quotes_task_args(template, task_args, expected):
    result = render_command(template, task_args=task_args)
    assert result == expected


@pytest.mark.parametrize(
    "value",
    [
        "tests/unit; echo ARG_PWN",
        '100%^"done',
    ],
    ids=["posix-metacharacters", "windows-sensitive"],
)
def test_render_command_delegates_task_arg_quoting_to_conda(monkeypatch, value):
    calls: list[tuple[str, ...]] = []

    def fake_quote_for_shell(*arguments):
        calls.append(arguments)
        return f"quoted:{arguments[0]}"

    monkeypatch.setattr(template_mod, "quote_for_shell", fake_quote_for_shell)

    assert (
        template_mod.render_command(
            "echo {{ value }}",
            task_args={"value": value},
        )
        == f"echo quoted:{value}"
    )
    assert calls == [(value,)]


def test_render_command_quotes_multiple_task_args():
    result = render_command(
        "cp {{ source }} {{ destination }}",
        task_args={"source": "dist/app.whl", "destination": "tmp/out; echo ARG_PWN"},
    )
    assert result == (
        f"cp {quote_for_shell('dist/app.whl')} "
        f"{quote_for_shell('tmp/out; echo ARG_PWN')}"
    )


@pytest.mark.parametrize(
    "template",
    [
        "{% if conda.is_unix %}unix{% else %}win{% endif %}",
        "{% if pixi.is_unix %}unix{% else %}win{% endif %}",
    ],
    ids=["conda-namespace", "pixi-alias"],
)
def test_render_platform_conditional(template):
    result = render(template)
    expected = "win" if on_win else "unix"
    assert result == expected


def test_render_manifest_path(tmp_path):
    p = tmp_path / "conda.toml"
    result = render("{{ conda.manifest_path }}", manifest_path=p)
    assert result == str(p)


def test_render_platform_variable():
    assert render("{{ conda.platform }}") == context.subdir


def test_render_extra_context():
    """extra_context merges user-supplied variables into the template context."""
    result = render("{{ custom_var }}", extra_context={"custom_var": "hello"})
    assert result == "hello"


@pytest.mark.parametrize(
    ("items", "task_args", "expected"),
    [
        (
            ["src/{{ name }}.py", "tests/"],
            {"name": "main"},
            ["src/main.py", "tests/"],
        ),
        ([], {}, []),
    ],
    ids=["with-vars", "empty"],
)
def test_render_list(items, task_args, expected):
    assert render_list(items, task_args=task_args) == expected
