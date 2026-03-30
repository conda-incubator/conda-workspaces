"""Shared status helpers for CLI output.

Provides verb-based status messages that are accessible and
colorblind-safe.  Every state is distinguishable by word alone;
color is supplementary, using blue/cyan/yellow instead of green/red.

Example output::

    Running lint task...
    All checks passed!
    Finished lint task

    Failed build task
    Skipped format-check task (cached)
    Would run lint task

    Installing default environment...
    Installed default environment
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape as _escape

if TYPE_CHECKING:
    from rich.console import Console


def _format(
    verb: str,
    noun: str,
    name: str,
    *,
    style: str = "",
    ellipsis: bool = False,
    detail: str | None = None,
    suffix: str | None = None,
) -> str:
    """Build a Rich markup status line.

    *verb* is the action word (``Running``, ``Finished``, …),
    *noun* is the object type (``task``, ``environment``),
    *name* is the specific item.
    """
    if style:
        text = f"[{style}]{verb}[/{style}]"
    else:
        text = verb
    text += f" [bold]{_escape(name)}[/bold] {noun}"
    if ellipsis:
        text += "[dim]...[/dim]"
    if detail:
        text += f"  [dim]{_escape(detail)}[/dim]"
    if suffix:
        text += f" [dim]({suffix})[/dim]"
    return text


def message(
    console: Console,
    verb: str,
    noun: str,
    name: str,
    *,
    style: str = "bold cyan",
    ellipsis: bool = False,
    detail: str | None = None,
    suffix: str | None = None,
) -> None:
    """Print a verb-based status line.

    Examples::

        status.message(console, "Running", "task", "lint",
                       style="bold blue", ellipsis=True)
        # -> Running lint task...

        status.message(console, "Installed", "environment", "default")
        # -> Installed default environment
    """
    console.print(
        _format(
            verb,
            noun,
            name,
            style=style,
            ellipsis=ellipsis,
            detail=detail,
            suffix=suffix,
        )
    )


def message_label(
    verb: str,
    noun: str,
    name: str,
    *,
    style: str = "bold yellow",
    detail: str | None = None,
) -> str:
    """Build a status label string for Rich Tree nodes."""
    return _format(verb, noun, name, style=style, detail=detail)
