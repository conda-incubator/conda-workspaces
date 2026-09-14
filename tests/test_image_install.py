"""Behavior of the build-stage installer and generated runtime entrypoint."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from conda.models.records import PackageRecord

import conda_workspaces._image_install as image_install
from conda_workspaces._image_install import ImageInstaller
from conda_workspaces.exceptions import CondaWorkspacesError
from conda_workspaces.models import WorkspaceConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any


@pytest.fixture
def image_activation(tmp_path: Path) -> dict[str, Any]:
    hook = tmp_path / "hook with ' quote.sh"
    hook.write_text('export HOOK_VALUE="${VALUE} from hook"\n', encoding="utf-8")
    return {
        "path": {
            "PATH": [
                "/opt/workspace/.conda/envs/default/bin",
                "/opt/conda/condabin",
                "/__workspace_runtime_path__",
            ]
        },
        "vars": {
            "export": {
                "CONDA_PREFIX": "/opt/workspace/.conda/envs/default",
                "CONDA_EXE": "/opt/conda/bin/conda",
                "_CONDA_ROOT": "/opt/conda",
                "VALUE": "a ' quote, $HOME, $(exit 9), `exit 8`\nand newline",
            },
            "unset": ["TO_REMOVE"],
            "set": {},
        },
        "scripts": {"activate": [str(hook)]},
    }


@pytest.mark.skipif(shutil.which("bash") is None, reason="requires bash")
@pytest.mark.parametrize(
    "runtime_path", ["/usr/bin:/custom/bin", ""], ids=["runtime-path", "fallback-path"]
)
def test_activation_exec_preserves_argv_env_and_hooks(
    tmp_path: Path, image_activation: dict[str, Any], runtime_path: str
) -> None:
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text(
        ImageInstaller.activation_script(image_activation), encoding="utf-8"
    )
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
    assert environment["CONDA_PREFIX"] == "/opt/workspace/.conda/envs/default"
    assert environment["PATH"].startswith("/opt/workspace/.conda/envs/default/bin:")
    assert environment["PATH"].endswith(runtime_path or "/sbin:/bin")
    assert not {"TO_REMOVE", "CONDA_EXE", "_CONDA_ROOT"} & environment.keys()
    assert "/opt/conda" not in environment["PATH"]


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
        ImageInstaller.activation_script(image_activation)


@pytest.fixture
def image_installer(tmp_path: Path) -> ImageInstaller:
    return ImageInstaller(WorkspaceConfig(root=str(tmp_path)), "default", "linux-64")


@pytest.mark.parametrize(
    "missing_style",
    ["return", "exception"],
    ids=["missing-backend", "missing-build-package"],
)
def test_local_build_requirements_must_already_be_locked(
    image_installer: ImageInstaller, missing_style: str
) -> None:
    class MissingDependencyError(Exception):
        dependencies = ["build"]

    def check(requirements: set[str], *, prefix: Path) -> list[str]:
        assert prefix == image_installer.prefix
        if missing_style == "exception":
            raise MissingDependencyError
        return list(requirements)

    dependencies = SimpleNamespace(
        check_dependencies=check, MissingDependencyError=MissingDependencyError
    )
    with pytest.raises(CondaWorkspacesError, match="absent.*locked environment"):
        image_installer.require_dependencies({"setuptools>=70"}, dependencies)


@pytest.fixture
def image_install_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SimpleNamespace, list[object]]:
    calls: list[object] = []
    plan = SimpleNamespace(
        records=[],
        update_path_dependencies=True,
        resolved=None,
        execute=lambda: calls.append("execute"),
    )

    def prepare(ctx: object, environment: str, *, prefix: Path) -> SimpleNamespace:
        calls.append((ctx, environment, prefix))
        return plan

    monkeypatch.setattr(
        image_install, "LockfileInstallPlan", SimpleNamespace(prepare=prepare)
    )
    monkeypatch.setattr(
        image_install,
        "conda_context",
        SimpleNamespace(
            plugin_manager=SimpleNamespace(get_virtual_package_records=lambda: [])
        ),
    )
    return plan, calls


@pytest.mark.parametrize(
    "compatible", [True, False], ids=["compatible", "missing-glibc"]
)
def test_install_validates_native_virtuals_and_defers_local_builds(
    image_installer: ImageInstaller,
    image_install_plan: tuple[SimpleNamespace, list[object]],
    compatible: bool,
) -> None:
    plan, calls = image_install_plan
    if not compatible:
        plan.records = [
            PackageRecord(
                name="example",
                version="1",
                build="0",
                build_number=0,
                depends=["__glibc >=2.99"],
            )
        ]
        with pytest.raises(CondaWorkspacesError, match="does not satisfy __glibc"):
            image_installer.install_packages()
        assert "execute" not in calls
    else:
        image_installer.install_packages()
        assert calls[-1] == "execute"
    assert plan.update_path_dependencies is False
    assert plan.resolved is image_installer.resolved


@pytest.fixture
def local_image_build(
    image_installer: ImageInstaller,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ImageInstaller, list[str], Callable[[str], None]]:
    import build
    from conda_pypi import build as conda_build
    from conda_pypi import dependencies, installer

    from conda_workspaces.models import PyPIDependency

    image_installer.resolved.pypi_dependencies = {
        "app": PyPIDependency("app", path=".", extras=("server",))
    }
    calls: list[str] = []
    missing: set[str] = set()

    class ProjectBuilder:
        build_system_requires = {"setuptools"}

        def __init__(self, source: Path, *, python_executable: str) -> None:
            assert source == Path(image_installer.config.root)
            assert python_executable == str(image_installer.prefix / "bin/python")

        def get_requires_for_build(self, distribution: str) -> set[str]:
            assert distribution == "wheel"
            return {"wheel"}

        def build(self, distribution: str, output: Path) -> str:
            calls.append("build")
            return str(output / "app.whl")

    def check(requirements: set[str], *, prefix: Path) -> list[str]:
        calls.extend(sorted(requirements))
        return sorted(requirements & missing)

    def install(prefix: Path, package: Path) -> None:
        calls.append("install")

    monkeypatch.setattr(build, "ProjectBuilder", ProjectBuilder)
    monkeypatch.setattr(dependencies, "check_dependencies", check)
    monkeypatch.setattr(
        conda_build, "build_conda", lambda *args, **kwargs: Path("app.conda")
    )
    monkeypatch.setattr(installer, "install_ephemeral_conda", install)
    return image_installer, calls, missing.add


@pytest.mark.parametrize(
    "missing",
    [None, "setuptools", "wheel", "app[server]"],
    ids=["locked", "build-system", "backend", "runtime-extra"],
)
def test_local_build_checks_backend_and_runtime_dependencies_without_solving(
    local_image_build: tuple[ImageInstaller, list[str], Callable[[str], None]],
    missing: str | None,
) -> None:
    image_installer, calls, set_missing = local_image_build
    if missing is None:
        image_installer.install_local_packages()
        assert calls == ["setuptools", "wheel", "build", "install", "app[server]"]
    else:
        set_missing(missing)
        with pytest.raises(CondaWorkspacesError, match="requirements are absent"):
            image_installer.install_local_packages()
        if missing == "app[server]":
            assert calls[-2:] == ["install", "app[server]"]
        else:
            assert "install" not in calls
