"""Terminal-safe rendering for repository-controlled values."""

from __future__ import annotations

import re

from rich.markup import escape as _escape

_TERMINAL_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def escape_for_console(value: object) -> str:
    """Escape Rich markup and render terminal controls as visible text.

    This lives outside the CLI package because core environment operations can
    write repository-controlled values through logging before a CLI renderer
    receives them.
    """
    text = _TERMINAL_CONTROL_RE.sub(
        lambda match: f"\\x{ord(match.group(0)):02x}",
        str(value),
    )
    return _escape(text)
