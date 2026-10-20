"""Tests for conda_workspaces.template."""

from __future__ import annotations

import pytest
from conda.base.constants import on_win
from conda.base.context import context

from conda_workspaces.exceptions import CondaWorkspacesError
from conda_workspaces.template import render, render_command, render_list


def test_render_no_template_fast_path():
    assert render("echo hello") == "echo hello"


def test_render_simple_variable():
    result = render("echo {{ greeting }}", task_args={"greeting": "hi"})
    assert result == "echo hi"


@pytest.mark.parametrize(
    "command",
    [
        "echo {{ value }}",
        'echo "{{ value }}"',
        "echo '{{ value }}'",
        "echo prefix{{ value }}suffix",
        "cat <<EOF\n{{ value }}\nEOF",
        "echo {{ value | upper }}",
        "echo {{ value | replace(\"'\", '') }}",
        "echo {{ value[1:] }}",
        "{% set alias = value %}echo {{ alias }}",
        "{% macro show(x) %}echo {{ x }}{% endmacro %}{{ show(value) }}",
        "{% if value %}echo yes{% endif %}",
    ],
    ids=[
        "unquoted",
        "double-quoted",
        "single-quoted",
        "concatenated",
        "heredoc",
        "filter",
        "quote-removing-filter",
        "substring",
        "alias",
        "macro",
        "conditional",
    ],
)
@pytest.mark.parametrize(
    "value", ["ordinary value", "$(printf injected)"], ids=["ordinary", "substitution"]
)
def test_render_command_rejects_named_argument_references(command, value):
    with pytest.raises(CondaWorkspacesError, match="array of strings"):
        render_command(command, task_args={"value": value})


@pytest.mark.parametrize(
    "name", ["range", "dict", "cycler", "joiner", "namespace", "lipsum"]
)
def test_render_command_rejects_arguments_shadowing_jinja_globals(name):
    with pytest.raises(CondaWorkspacesError, match="array of strings"):
        render_command(
            "echo {{ " + name + " }}", task_args={name: "$(printf injected)"}
        )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo hello", "echo hello"),
        ("echo {{ conda.platform }}", f"echo {context.subdir}"),
        ("echo {{ pixi.platform }}", f"echo {context.subdir}"),
        (
            "{% if conda.is_unix %}unix{% else %}win{% endif %}",
            "win" if on_win else "unix",
        ),
    ],
    ids=["static", "conda-context", "pixi-context", "platform-conditional"],
)
def test_render_command_allows_args_used_outside_shell_source(command, expected):
    assert (
        render_command(
            command,
            task_args={
                "value": "$(printf injected)",
                "conda": "ignored",
                "pixi": "ignored",
            },
        )
        == expected
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("{% set value = 'fixed' %}echo {{ value }}", "echo fixed"),
        ("{% for value in ['fixed'] %}echo {{ value }}{% endfor %}", "echo fixed"),
        (
            "{% macro show(value) %}echo {{ value }}{% endmacro %}{{ show('fixed') }}",
            "echo fixed",
        ),
    ],
    ids=["assignment", "loop", "macro"],
)
def test_render_command_preserves_template_local_names(command, expected):
    assert (
        render_command(command, task_args={"value": "$(printf injected)"}) == expected
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
        (
            ["{{ value | upper }}", "{% if value == 'hello' %}yes{% endif %}"],
            {"value": "hello"},
            ["HELLO", "yes"],
        ),
        ([], {}, []),
    ],
    ids=["with-vars", "filters-and-conditionals", "empty"],
)
def test_render_list(items, task_args, expected):
    assert render_list(items, task_args=task_args) == expected
