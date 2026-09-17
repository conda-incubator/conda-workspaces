"""Freeze conda activation for images whose runtime does not include conda."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context

from .exceptions import CondaWorkspacesError

if TYPE_CHECKING:
    from typing import Any


def activation_script(activation: dict[str, Any]) -> str:
    """Render conda's activation data as a quoted bash entrypoint."""
    lines = ["#!/bin/bash", "set -e"]
    bootstrap_variables = set(conda_context.conda_exe_vars_dict) | {
        "CONDA_PROMPT_MODIFIER"
    }
    unset = set(activation["vars"]["unset"]) | bootstrap_variables
    exports = activation["vars"]["export"]
    for name in [*unset, *exports]:
        if not re.fullmatch("[A-Za-z_][A-Za-z0-9_]*", name):
            raise CondaWorkspacesError("Invalid activation environment variable name.")
    if unset:
        lines.append("unset " + " ".join(sorted(unset)))
    paths = [
        value
        for value in activation["path"]["PATH"]
        if not value.startswith("/opt/conda/")
    ]
    runtime_marker = "/__workspace_runtime_path__"
    runtime_path = (
        '"${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"'
    )
    lines.append(
        "export PATH="
        + ":".join(
            runtime_path if value == runtime_marker else shlex.quote(value)
            for value in paths
        )
    )
    for name, value in exports.items():
        if name not in bootstrap_variables:
            lines.append(f"export {name}={shlex.quote(str(value))}")
    for script in activation["scripts"]["activate"]:
        lines.append(". " + shlex.quote(script))
    lines.append('exec "$@"')
    return "\n".join(lines) + "\n"


def main() -> None:
    """Write an entrypoint using activation metadata from the installed prefix."""
    prefix, destination = map(Path, sys.argv[1:])
    activation = subprocess.run(
        [sys.executable, "-m", "conda", "shell.posix+json", "activate", str(prefix)],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/__workspace_runtime_path__", "CONDA_CHANGEPS1": "false"},
    )
    destination.write_text(
        activation_script(json.loads(activation.stdout)), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
