"""Install the locked prefix and freeze conda's activation instructions."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from conda_workspaces.context import WorkspaceContext
from conda_workspaces.lockfile import LockfileInstallPlan, lockfile_status
from conda_workspaces.models import LockfileStatus


def main() -> None:
    """Install one current locked environment and write its runtime entrypoint."""
    context = WorkspaceContext()
    status = lockfile_status(context, context.config)
    if status.status != LockfileStatus.UP_TO_DATE:
        raise RuntimeError(f"A current conda.lock is required: {status.reason}")
    prefix = Path("/opt/workspace/.conda/envs/runtime")
    plan = LockfileInstallPlan.prepare(context, "runtime", prefix=prefix)
    assert plan.resolved is not None
    plan.resolved.activation_scripts = [
        str(context.root / script) for script in plan.resolved.activation_scripts
    ]
    plan.execute()
    activation = json.loads(
        subprocess.check_output(
            ["/opt/conda/bin/conda", "shell.posix+json", "activate", str(prefix)],
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            },
            text=True,
        )
    )
    lines = ["#!/bin/bash", "set -e", f'export PATH="{prefix}/bin:${{PATH}}"']
    metadata = {
        "CONDA_EXE",
        "CONDA_PYTHON_EXE",
        "_CONDA_EXE",
        "_CONDA_ROOT",
        "_CE_M",
        "_CE_CONDA",
    }
    for name in activation["vars"]["unset"]:
        lines.append(f"unset {shlex.quote(name)}")
    for name, value in activation["vars"]["export"].items():
        if name not in metadata:
            lines.append(f"export {name}={shlex.quote(str(value))}")
    for script in activation["scripts"]["activate"]:
        lines.append(f". {shlex.quote(script)}")
    lines.append('exec "$@"')
    entrypoint = Path("/entrypoint.sh")
    entrypoint.write_text("\n".join(lines) + "\n")
    entrypoint.chmod(0o755)


if __name__ == "__main__":
    main()
