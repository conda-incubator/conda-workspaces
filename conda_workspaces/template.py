"""Jinja2 template rendering for task commands and paths."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from conda.utils import quote_for_shell

from .context import build_template_context

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
) -> str:
    """Render a Jinja2 template string with the conda-workspaces template context.

    If *template_str* contains no template markers it is returned as-is
    (fast path that avoids Jinja2 import entirely).
    """
    if "{{" not in template_str and "{%" not in template_str:
        return template_str

    env = _get_jinja_env()
    ctx = build_template_context(manifest_path=manifest_path, task_args=task_args)
    if extra_context:
        ctx.update(extra_context)
    tpl = env.from_string(template_str)
    return tpl.render(ctx)


def render_command(
    template_str: str,
    manifest_path: Path | None = None,
    task_args: dict[str, str] | None = None,
) -> str:
    """Render a shell command template with task arguments shell-quoted.

    Command strings are executed through the native shell, so named task
    arguments are data values that must occupy one shell word when they
    are interpolated.
    """
    quoted_args = (
        {key: quote_for_shell(str(value)) for key, value in task_args.items()}
        if task_args
        else None
    )
    return render(template_str, manifest_path=manifest_path, task_args=quoted_args)


def render_list(
    items: list[str],
    manifest_path: Path | None = None,
    task_args: dict[str, str] | None = None,
) -> list[str]:
    """Render each string in *items* through the template engine."""
    return [render(s, manifest_path=manifest_path, task_args=task_args) for s in items]
