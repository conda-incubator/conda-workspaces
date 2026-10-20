"""Jinja2 template rendering for task commands and paths."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from .context import build_template_context
from .exceptions import CondaWorkspacesError

if TYPE_CHECKING:
    from pathlib import Path

    from jinja2 import Environment as JinjaEnvironment


@lru_cache(maxsize=1)
def _get_jinja_env() -> JinjaEnvironment:
    """Create and cache a sandboxed Jinja2 Environment (singleton)."""
    from jinja2 import StrictUndefined
    from jinja2.sandbox import SandboxedEnvironment

    return SandboxedEnvironment(undefined=StrictUndefined)


def render(
    template_str: str,
    manifest_path: Path | None = None,
    task_args: dict[str, str] | None = None,
    extra_context: dict[str, object] | None = None,
    target_prefix: Path | None = None,
) -> str:
    """Render a Jinja2 template string with the conda-workspaces template context.

    If *template_str* contains no template markers it is returned as-is
    (fast path that avoids Jinja2 import entirely).
    """
    if "{{" not in template_str and "{%" not in template_str:
        return template_str

    env = _get_jinja_env()
    ctx = build_template_context(
        manifest_path=manifest_path,
        task_args=task_args,
        target_prefix=target_prefix,
    )
    if extra_context:
        ctx.update(extra_context)
    tpl = env.from_string(template_str)
    return tpl.render(ctx)


def render_command(
    template_str: str,
    manifest_path: Path | None = None,
    task_args: dict[str, str] | None = None,
    target_prefix: Path | None = None,
) -> str:
    """Render shell source without inserting named task argument data.

    Named arguments belong in argv-list commands or task environment values.
    Quoting before template rendering cannot protect every shell syntax context.
    """
    if task_args and ("{{" in template_str or "{%" in template_str):
        from jinja2 import meta

        argument_names = task_args.keys() - {"conda", "pixi"}
        env = _get_jinja_env().overlay()
        # Jinja excludes globals from undeclared names, but arguments can shadow them.
        env.globals = {
            key: value
            for key, value in env.globals.items()
            if key not in argument_names
        }
        referenced_names = meta.find_undeclared_variables(env.parse(template_str))
        if argument_names.intersection(referenced_names):
            raise CondaWorkspacesError(
                "Named task arguments cannot be referenced in shell command templates.",
                hints=[
                    'Use an array of strings for cmd: ["pytest", "{{ path }}"].',
                    "For shell tasks, pass data through env for the program to read.",
                ],
            )
    return render(
        template_str,
        manifest_path=manifest_path,
        target_prefix=target_prefix,
    )


def render_list(
    items: list[str],
    manifest_path: Path | None = None,
    task_args: dict[str, str] | None = None,
    target_prefix: Path | None = None,
) -> list[str]:
    """Render each string in *items* through the template engine."""
    return [
        render(
            item,
            manifest_path=manifest_path,
            task_args=task_args,
            target_prefix=target_prefix,
        )
        for item in items
    ]
