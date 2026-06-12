"""Shell execution backend for running task commands."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from conda.base.constants import on_win
from conda.base.context import context
from conda.gateways.subprocess import subprocess_call
from conda.utils import wrap_subprocess_call

Command = str | list[str]


class SubprocessShell:
    """Execute shell commands, optionally inside an activated conda env.

    When *conda_prefix* is given the command is executed inside an
    activated conda environment (mirroring ``conda run``).  Otherwise
    the command runs directly in the current shell.
    """

    def run(
        self,
        cmd: Command,
        env: dict[str, str],
        cwd: Path,
        conda_prefix: Path | None = None,
        clean_env: bool = False,
    ) -> int:
        """Execute *cmd* and return the process exit code."""
        run_env = self._build_env(env, clean_env)

        if conda_prefix is not None:
            return self._run_in_env(cmd, run_env, cwd, conda_prefix)
        return self._run_direct(cmd, run_env, cwd)

    def _build_env(self, extra: dict[str, str], clean: bool) -> dict[str, str]:
        """Build the environment variable mapping for a subprocess."""
        if clean:
            base: dict[str, str] = {}
            for key in (
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
            ):
                val = os.environ.get(key)
                if val is not None:
                    base[key] = val
        else:
            base = dict(os.environ)
        base.update(extra)
        return base

    def _run_direct(self, cmd: Command, env: dict[str, str], cwd: Path) -> int:
        """Run *cmd* without conda activation."""
        script: Path | None = None
        try:
            command, script = self._direct_command(cmd)
            result = subprocess_call(
                command,
                env=env,
                path=cwd,
                raise_on_error=False,
                capture_output=False,
            )
            return result.rc
        finally:
            self._unlink_script(script)

    def _run_in_env(
        self,
        cmd: Command,
        env: dict[str, str],
        cwd: Path,
        conda_prefix: Path,
    ) -> int:
        """Run *cmd* inside an activated conda environment at *conda_prefix*."""
        root_prefix = context.root_prefix
        dev_mode = context.dev
        debug_wrapper_scripts: bool = getattr(context, "debug_wrapper_scripts", False)

        script, command = wrap_subprocess_call(
            root_prefix,
            str(conda_prefix),
            dev_mode,
            debug_wrapper_scripts,
            self._activation_command(cmd),
        )
        try:
            result = subprocess_call(
                command,
                env=env,
                path=cwd,
                raise_on_error=False,
                capture_output=False,
            )
            return result.rc
        finally:
            self._unlink_script(Path(script) if script else None)

    @classmethod
    def _direct_command(cls, cmd: Command) -> tuple[list[str], Path | None]:
        """Return subprocess argv for a direct command and an optional temp script."""
        if isinstance(cmd, list):
            return cmd, None
        if on_win:
            script = cls._write_windows_batch(cmd)
            return ["cmd", "/d", "/c", str(script)], script
        return cls._shell_command(cmd), None

    @classmethod
    def _activation_command(cls, cmd: Command) -> list[str]:
        """Return command arguments for conda's activation wrapper."""
        if isinstance(cmd, list):
            return cmd
        if on_win:
            return [cls._batch_body(cmd)]
        return cls._shell_command(cmd)

    @classmethod
    def _write_windows_batch(cls, cmd: str) -> Path:
        """Write *cmd* to a temporary batch script and return its path."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".bat",
            delete=False,
        ) as handle:
            handle.write("@ECHO OFF\n")
            handle.write(cls._batch_body(cmd))
            handle.write('SET "_CONDA_WORKSPACES_RC=%ERRORLEVEL%"\n')
            handle.write("EXIT /B %_CONDA_WORKSPACES_RC%\n")
            return Path(handle.name)

    @staticmethod
    def _batch_body(cmd: str) -> str:
        """Return *cmd* with a final newline for raw batch-script insertion."""
        return cmd if cmd.endswith("\n") else f"{cmd}\n"

    @staticmethod
    def _unlink_script(script: Path | None) -> None:
        if script and script.exists():
            try:
                script.unlink()
            except OSError:
                pass

    @staticmethod
    def _shell_command(cmd: str) -> list[str]:
        """Wrap *cmd* in the platform-appropriate shell invocation."""
        if on_win:
            return ["cmd", "/d", "/c", cmd]
        return [os.environ.get("SHELL", "/bin/sh"), "-c", cmd]
