"""Tests for conda_workspaces.runner."""

from __future__ import annotations

from pathlib import Path

import pytest
from conda.base.constants import on_win

from conda_workspaces.runner import SubprocessShell


def test_run_simple_command(tmp_path):
    shell = SubprocessShell()
    marker = tmp_path / "marker.txt"
    script = tmp_path / "_write.py"
    script.write_text(
        "from pathlib import Path\nPath('marker.txt').write_text('done')\n"
    )
    exit_code = shell.run("python _write.py", {}, tmp_path)
    assert exit_code == 0
    assert marker.exists()


@pytest.mark.parametrize("code", [0, 1, 42])
def test_run_returns_exit_code(tmp_path, code):
    shell = SubprocessShell()
    exit_code = shell.run(f"exit {code}", {}, tmp_path)
    assert exit_code == code


def test_run_with_env(tmp_path):
    shell = SubprocessShell()
    marker = tmp_path / "envtest.txt"
    script = tmp_path / "_envwrite.py"
    script.write_text(
        "import os\nfrom pathlib import Path\n"
        "Path('envtest.txt').write_text(os.environ['MY_VAR'])\n"
    )
    exit_code = shell.run("python _envwrite.py", {"MY_VAR": "hello123"}, tmp_path)
    assert exit_code == 0
    content = marker.read_text().strip()
    assert "hello123" in content


def test_run_with_cwd(tmp_path):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    shell = SubprocessShell()
    cmd = "cd" if on_win else "pwd"
    exit_code = shell.run(cmd, {}, subdir)
    assert exit_code == 0


@pytest.mark.parametrize(
    ("extra_env", "clean", "expected_key", "expected_val"),
    [
        ({}, True, "PATH", None),
        ({"FOO": "bar"}, True, "FOO", "bar"),
        ({"FOO": "bar"}, False, "FOO", "bar"),
    ],
    ids=["clean-has-path", "clean-with-extras", "non-clean-with-extras"],
)
def test_build_env(extra_env, clean, expected_key, expected_val):
    shell = SubprocessShell()
    env = shell._build_env(extra_env, clean=clean)
    assert expected_key in env
    if expected_val is not None:
        assert env[expected_key] == expected_val
    if clean:
        allowed = {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "TERM",
            "LANG",
            "SYSTEMROOT",
            "COMSPEC",
            "TEMP",
            "TMP",
        } | set(extra_env)
        for key in env:
            assert key in allowed


def test_list_command(tmp_path):
    shell = SubprocessShell()
    exit_code = shell.run(["echo", "hello"], {}, tmp_path)
    assert exit_code == 0


@pytest.mark.parametrize(
    ("cmd", "expected_command"),
    [
        ("echo hi", SubprocessShell._shell_command("echo hi")),
        (
            ["python", "-c", "print(1); echo ARRAY_PWN"],
            ["python", "-c", "print(1); echo ARRAY_PWN"],
        ),
    ],
    ids=["shell-string", "argv-list"],
)
def test_run_direct_command_contract(tmp_path, monkeypatch, cmd, expected_command):
    import types

    import conda_workspaces.runner as runner_mod

    calls: list[tuple] = []
    script_bodies: list[str] = []

    def fake_subprocess_call(command, **kwargs):
        calls.append((command, kwargs))
        if isinstance(cmd, str) and on_win:
            script_bodies.append(Path(command[3]).read_text())
        return types.SimpleNamespace(rc=0)

    monkeypatch.setattr(runner_mod, "subprocess_call", fake_subprocess_call)

    shell = SubprocessShell()
    exit_code = shell.run(cmd, {}, tmp_path)

    assert exit_code == 0
    if isinstance(cmd, str) and on_win:
        assert calls[0][0][:3] == ["cmd", "/d", "/c"]
        assert script_bodies[0].startswith("@ECHO OFF\n")
        assert "echo hi\n" in script_bodies[0]
        assert not Path(calls[0][0][3]).exists()
    else:
        assert calls[0][0] == expected_command
    assert calls[0][1]["capture_output"] is False


def test_run_direct_windows_string_uses_batch_script(tmp_path, monkeypatch):
    import types

    import conda_workspaces.runner as runner_mod

    calls: list[tuple] = []
    script_bodies: list[str] = []

    def fake_subprocess_call(command, **kwargs):
        calls.append((command, kwargs))
        script_bodies.append(Path(command[3]).read_text())
        return types.SimpleNamespace(rc=0)

    monkeypatch.setattr(runner_mod, "on_win", True)
    monkeypatch.setattr(runner_mod, "subprocess_call", fake_subprocess_call)

    shell = SubprocessShell()
    code = shell.run('python -c "print(1)"', {}, tmp_path)

    assert code == 0
    assert calls[0][0][:3] == ["cmd", "/d", "/c"]
    assert script_bodies[0] == (
        "@ECHO OFF\n"
        'python -c "print(1)"\n'
        'SET "_CONDA_WORKSPACES_RC=%ERRORLEVEL%"\n'
        "EXIT /B %_CONDA_WORKSPACES_RC%\n"
    )
    assert not Path(calls[0][0][3]).exists()


@pytest.mark.skipif(on_win, reason="Unix-only test")
def test_shell_command_unix():
    result = SubprocessShell._shell_command("echo hi")
    assert result[-1] == "echo hi"
    assert "-c" in result


@pytest.mark.skipif(not on_win, reason="Windows-only test")
def test_shell_command_windows():
    result = SubprocessShell._shell_command("echo hi")
    assert result[0] == "cmd"
    assert "/c" in result


@pytest.mark.parametrize(
    ("cmd", "expected_arguments"),
    [
        ("echo hi", SubprocessShell._shell_command("echo hi")),
        (
            ["python", "-c", "print(1); echo ARRAY_PWN"],
            ["python", "-c", "print(1); echo ARRAY_PWN"],
        ),
    ],
    ids=["shell-string", "argv-list"],
)
def test_run_in_env_delegates_to_conda_wrapper(
    tmp_path,
    monkeypatch,
    cmd,
    expected_arguments,
):
    """_run_in_env delegates activation wrapping to conda."""
    import types

    import conda_workspaces.runner as runner_mod

    script_file = tmp_path / "activate.sh"
    script_file.write_text("#!/bin/sh\n")

    fake_context = types.SimpleNamespace(
        root_prefix=str(tmp_path / "root"),
        dev=False,
    )

    wrap_calls: list[tuple] = []

    def fake_wrap(*args):
        wrap_calls.append(args)
        return (str(script_file), ["echo", "hi"])

    def fake_subprocess_call(*args, **kwargs):
        return types.SimpleNamespace(rc=0)

    monkeypatch.setattr(runner_mod, "context", fake_context)
    monkeypatch.setattr(runner_mod, "wrap_subprocess_call", fake_wrap)
    monkeypatch.setattr(runner_mod, "subprocess_call", fake_subprocess_call)

    shell = SubprocessShell()
    code = shell._run_in_env(cmd, {}, tmp_path, tmp_path / "envs/test")

    assert code == 0
    assert len(wrap_calls) == 1
    if isinstance(cmd, str) and on_win:
        assert wrap_calls[0][4] == [SubprocessShell._batch_body(cmd)]
    else:
        assert wrap_calls[0][4] == expected_arguments


def test_run_in_env_windows_string_uses_activation_script_body(tmp_path, monkeypatch):
    import types

    import conda_workspaces.runner as runner_mod

    script_file = tmp_path / "activate.bat"
    script_file.write_text("@ECHO OFF\n")

    fake_context = types.SimpleNamespace(
        root_prefix=str(tmp_path / "root"),
        dev=False,
    )
    wrap_calls: list[tuple] = []

    def fake_wrap(*args):
        wrap_calls.append(args)
        return (str(script_file), ["cmd", "/d", "/c", str(script_file)])

    monkeypatch.setattr(runner_mod, "on_win", True)
    monkeypatch.setattr(runner_mod, "context", fake_context)
    monkeypatch.setattr(runner_mod, "wrap_subprocess_call", fake_wrap)
    monkeypatch.setattr(
        runner_mod,
        "subprocess_call",
        lambda *a, **kw: types.SimpleNamespace(rc=0),
    )

    shell = SubprocessShell()
    code = shell._run_in_env(
        'python -c "print(1)"',
        {},
        tmp_path,
        tmp_path / "envs/test",
    )

    assert code == 0
    assert wrap_calls[0][4] == ['python -c "print(1)"\n']
    assert not script_file.exists()


def test_run_in_env_cleans_up_script(tmp_path, monkeypatch):
    """_run_in_env removes the wrapper script after execution."""
    import types

    import conda_workspaces.runner as runner_mod

    script_file = tmp_path / "wrapper_script.sh"
    script_file.write_text("#!/bin/sh\n")

    fake_context = types.SimpleNamespace(
        root_prefix=str(tmp_path),
        dev=False,
    )

    monkeypatch.setattr(runner_mod, "context", fake_context)
    monkeypatch.setattr(
        runner_mod,
        "wrap_subprocess_call",
        lambda *a: (str(script_file), ["echo", "hi"]),
    )
    monkeypatch.setattr(
        runner_mod,
        "subprocess_call",
        lambda *a, **kw: types.SimpleNamespace(rc=0),
    )

    shell = SubprocessShell()
    shell._run_in_env("echo hi", {}, tmp_path, tmp_path / "env")

    assert not script_file.exists()


def test_run_delegates_to_env_when_prefix_given(tmp_path, monkeypatch):
    """SubprocessShell.run calls _run_in_env when conda_prefix is set."""
    called_with: list[tuple] = []

    def fake_run_in_env(cmd, env, cwd, conda_prefix):
        called_with.append((cmd, env, cwd, conda_prefix))
        return 0

    shell = SubprocessShell()
    monkeypatch.setattr(shell, "_run_in_env", fake_run_in_env)
    code = shell.run("echo hi", {}, tmp_path, conda_prefix=tmp_path / "env")

    assert code == 0
    assert len(called_with) == 1
