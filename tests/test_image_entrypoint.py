"""Behavior of generated runtime activation entrypoints."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.exceptions import CondaWorkspacesError
from conda_workspaces.image_entrypoint import activation_script

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


@pytest.fixture
def image_activation(tmp_path: Path) -> dict[str, Any]:
    hook = tmp_path / "hook with ' quote.sh"
    hook.write_text('export HOOK_VALUE="${VALUE} from hook"\n', encoding="utf-8")
    return {
        "path": {
            "PATH": [
                "/workspaces/image-test/.conda/envs/default/bin",
                "/opt/conda/condabin",
                "/__workspace_runtime_path__",
            ]
        },
        "vars": {
            "export": {
                "CONDA_PREFIX": "/workspaces/image-test/.conda/envs/default",
                "CONDA_EXE": "/opt/conda/bin/conda",
                "_CONDA_ROOT": "/opt/conda",
                "VALUE": "a ' quote, $HOME, $(exit 9), `exit 8`\nand newline",
            },
            "unset": ["TO_REMOVE"],
            "set": {},
        },
        "scripts": {"activate": [str(hook)]},
    }


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None, reason="requires POSIX bash"
)
@pytest.mark.parametrize(
    "runtime_path",
    ["/workspaces/image-test/.conda/bin:/usr/bin:/custom/bin", ""],
    ids=["runtime-path", "fallback-path"],
)
def test_activation_exec_preserves_argv_env_and_hooks(
    tmp_path: Path, image_activation: dict[str, Any], runtime_path: str
) -> None:
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text(activation_script(image_activation), encoding="utf-8")
    code = (
        "import json,os,sys; "
        "print(json.dumps([os.getpid(),dict(os.environ),sys.argv[1:]]))"
    )
    with subprocess.Popen(
        [
            shutil.which("bash"),
            str(entrypoint),
            sys.executable,
            "-c",
            code,
            "one argument",
            "$(exit 7)",
        ],
        env={
            "PATH": runtime_path,
            "TO_REMOVE": "stale",
            "CONDA_EXE": "/opt/conda/bin/conda",
        },
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        stdout, _ = process.communicate(timeout=10)
        assert process.returncode == 0
        pid, environment, argv = json.loads(stdout)
        assert pid == process.pid
    assert argv == ["one argument", "$(exit 7)"]
    assert environment["VALUE"] == image_activation["vars"]["export"]["VALUE"]
    assert environment["HOOK_VALUE"] == environment["VALUE"] + " from hook"
    assert environment["CONDA_PREFIX"] == "/workspaces/image-test/.conda/envs/default"
    assert environment["PATH"].startswith(
        "/workspaces/image-test/.conda/envs/default/bin:"
    )
    assert environment["PATH"].endswith(runtime_path or "/sbin:/bin")
    assert not {"TO_REMOVE", "CONDA_EXE", "_CONDA_ROOT"} & environment.keys()
    assert "/opt/conda" not in environment["PATH"]


def test_activation_replaces_only_the_runtime_path_element(
    image_activation: dict[str, Any],
) -> None:
    collision = "/workspaces/__workspace_runtime_path__/.conda/envs/default/bin"
    image_activation["path"]["PATH"] = [
        collision,
        "/__workspace_runtime_path__",
    ]

    path_export = next(
        line
        for line in activation_script(image_activation).splitlines()
        if line.startswith("export PATH=")
    )

    assert path_export.startswith(f"export PATH={collision}:")
    assert path_export.count("${PATH:-") == 1


@pytest.mark.parametrize(
    "name",
    ["BAD-NAME", "X=$(exit 1)", "X\nexit 1"],
    ids=["hyphen", "substitution", "newline"],
)
def test_activation_rejects_invalid_variable_name(
    image_activation: dict[str, Any], name: str
) -> None:
    image_activation["vars"]["export"][name] = "value"
    with pytest.raises(CondaWorkspacesError, match="variable name"):
        activation_script(image_activation)
