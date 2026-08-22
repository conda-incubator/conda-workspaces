"""Tests for conda_workspaces.cli.main — parser configuration and dispatch."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

import pytest
from conda.base.context import reset_context
from conda.exceptions import CondaError, CondaSystemExit, DryRunExit
from rich.console import Console

from conda_workspaces.cli.main import (
    execute_task,
    execute_workspace,
    generate_task_parser,
    generate_workspace_parser,
)
from conda_workspaces.exceptions import AttestationError


def test_generate_workspace_parser_returns_parser() -> None:
    parser = generate_workspace_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert "workspace" in parser.prog


def test_generate_task_parser_returns_parser() -> None:
    parser = generate_task_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert "task" in parser.prog


@pytest.mark.parametrize(
    "subcmd",
    [
        "init",
        "install",
        "lock",
        "attest",
        "verify",
        "sbom",
        "list",
        "envs",
        "info",
        "add",
        "update",
        "remove",
        "clean",
        "activate",
        "shell",
    ],
)
def test_workspace_subcommands_registered(subcmd: str) -> None:
    parser = generate_workspace_parser()
    if subcmd in ("add", "update", "remove"):
        args = parser.parse_args([subcmd, "numpy"])
    else:
        args = parser.parse_args([subcmd])
    assert args.subcmd == subcmd


@pytest.mark.parametrize(
    "subcmd",
    ["run", "list", "add", "remove", "export"],
)
def test_task_subcommands_registered(subcmd: str) -> None:
    parser = generate_task_parser()
    if subcmd == "run":
        args = parser.parse_args([subcmd, "test"])
    elif subcmd == "add":
        args = parser.parse_args([subcmd, "lint", "ruff check ."])
    elif subcmd == "remove":
        args = parser.parse_args([subcmd, "lint"])
    else:
        args = parser.parse_args([subcmd])
    assert args.subcmd == subcmd


def test_workspace_no_subcmd_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = generate_workspace_parser()
    args = parser.parse_args([])
    result = execute_workspace(args)
    assert result == 0
    captured = capsys.readouterr()
    assert "workspace" in captured.out.lower() or "usage" in captured.out.lower()


def test_task_no_subcmd_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = generate_task_parser()
    args = parser.parse_args([])
    result = execute_task(args)
    assert result == 0
    captured = capsys.readouterr()
    assert "task" in captured.out.lower() or "usage" in captured.out.lower()


def test_workspace_unknown_subcmd_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = generate_workspace_parser()
    args = parser.parse_args([])
    args.subcmd = "nonexistent"
    result = execute_workspace(args)
    assert result == 0


@pytest.mark.parametrize(
    "args, expected_attr, expected_value",
    [
        (["init", "--format", "conda"], "manifest_format", "conda"),
        (["init", "--format", "pyproject"], "manifest_format", "pyproject"),
        (["init", "--name", "myproj"], "name", "myproj"),
        (
            ["init", "-c", "defaults", "-c", "bioconda"],
            "channel",
            ["defaults", "bioconda"],
        ),
        (["init", "-c", "defaults", "--override-channels"], "override_channels", True),
        (
            ["quickstart", "-c", "defaults", "-c", "bioconda"],
            "channel",
            ["defaults", "bioconda"],
        ),
        (
            ["quickstart", "-c", "defaults", "--override-channels"],
            "override_channels",
            True,
        ),
        (["install", "-e", "test"], "environment", "test"),
        (["install", "--force-reinstall"], "force_reinstall", True),
        (["install", "--locked", "--verify"], "verify", True),
        (["lock", "--sign"], "sign", True),
        (
            ["lock", "--sign", "--attestation", "dist/conda.lock.sigstore.json"],
            "attestation",
            Path("dist/conda.lock.sigstore.json"),
        ),
        (
            ["attest", "--attestation", "dist/conda.lock.sigstore.json"],
            "attestation",
            Path("dist/conda.lock.sigstore.json"),
        ),
        (["attest", "--dry-run"], "dry_run", True),
        (["attest", "--json"], "json", True),
        (
            [
                "verify",
                "--cert-identity",
                "release@example.com",
                "--cert-oidc-issuer",
                "https://issuer.example",
            ],
            "cert_identity",
            "release@example.com",
        ),
        (
            ["verify", "--attestation", "dist/conda.lock.sigstore.json"],
            "attestation",
            Path("dist/conda.lock.sigstore.json"),
        ),
        (["verify", "--json"], "json", True),
        (["envs", "--installed"], "installed", True),
        (["info", "-e", "test"], "environment", "test"),
        (["info", "--packages"], "packages", True),
        (["info"], "environment", None),
        (["add", "--pypi", "requests"], "pypi", True),
        (["add", "--feature", "dev", "numpy"], "feature", "dev"),
        (["update", "--feature", "dev", "numpy"], "feature", "dev"),
        (["update", "--no-install", "numpy"], "no_install", True),
        (["remove", "--pypi", "requests"], "pypi", True),
        (["clean", "-e", "test"], "environment", "test"),
        (["activate", "-e", "docs"], "environment", "docs"),
        (["activate"], "environment", "default"),
        (["sbom", "--environment", "runtime"], "environment", "runtime"),
        (["sbom", "--platform", "linux-64"], "platform", "linux-64"),
        (["sbom", "--from-prefix"], "from_prefix", True),
        (["sbom", "--reproducible"], "reproducible", True),
        (["sbom"], "reproducible", False),
        (
            ["sbom", "--file", "dist/runtime.cdx.json"],
            "output",
            Path("dist/runtime.cdx.json"),
        ),
        (["sbom", "--product-name", "Acme Runtime"], "product_name", "Acme Runtime"),
        (["sbom", "--product-version", "2026.08"], "product_version", "2026.08"),
        (
            ["sbom", "--product-manufacturer", "Acme GmbH"],
            "product_manufacturer",
            "Acme GmbH",
        ),
        (
            ["sbom", "--product-manufacturer-url", "https://acme.example"],
            "product_manufacturer_url",
            "https://acme.example",
        ),
        (["sbom", "--author-name", "Alice Example"], "author_name", "Alice Example"),
        (
            ["sbom", "--author-email", "alice@acme.example"],
            "author_email",
            "alice@acme.example",
        ),
        (
            ["sbom", "--author-organization", "Acme Product Security"],
            "author_organization",
            "Acme Product Security",
        ),
        (
            ["sbom", "--author-organization-url", "https://acme.example/security"],
            "author_organization_url",
            "https://acme.example/security",
        ),
        (["sbom", "--dry-run"], "dry_run", True),
        (["sbom", "--json"], "json", True),
    ],
    ids=[
        "init-format-conda",
        "init-format-pyproject",
        "init-name",
        "init-channels",
        "init-override-channels",
        "quickstart-channels",
        "quickstart-override-channels",
        "install-env",
        "install-force",
        "install-verify",
        "lock-sign",
        "lock-attestation",
        "attest-attestation",
        "attest-dry-run",
        "attest-json",
        "verify-identity",
        "verify-attestation",
        "verify-json",
        "envs-installed",
        "info-named",
        "info-packages",
        "info-default",
        "add-pypi",
        "add-feature",
        "update-feature",
        "update-no-install",
        "remove-pypi",
        "clean-env",
        "activate-named",
        "activate-default",
        "sbom-environment",
        "sbom-platform",
        "sbom-prefix",
        "sbom-reproducible",
        "sbom-reproducible-default",
        "sbom-output",
        "sbom-product-name",
        "sbom-product-version",
        "sbom-manufacturer",
        "sbom-manufacturer-url",
        "sbom-author-name",
        "sbom-author-email",
        "sbom-author-organization",
        "sbom-author-organization-url",
        "sbom-dry-run",
        "sbom-json",
    ],
)
def test_workspace_parser_args(
    args: list[str], expected_attr: str, expected_value: object
) -> None:
    parser = generate_workspace_parser()
    parsed = parser.parse_args(args)
    assert getattr(parsed, expected_attr) == expected_value


@pytest.mark.parametrize("subcmd", ["add", "update", "remove"])
@pytest.mark.parametrize(
    "selectors",
    [
        pytest.param(["--platform", "linux-64"], id="base"),
        pytest.param(["--feature", "dev", "--platform", "linux-64"], id="feature"),
        pytest.param(
            ["--environment", "qa", "--platform", "linux-64"],
            id="environment",
        ),
    ],
)
def test_workspace_mutation_platform_parser_args(
    subcmd: str,
    selectors: list[str],
) -> None:
    parsed = generate_workspace_parser().parse_args([subcmd, *selectors, "numpy"])
    assert parsed.platform == "linux-64"


@pytest.mark.parametrize("subcmd", ["add", "update", "remove"])
def test_workspace_mutation_locations_are_mutually_exclusive(subcmd: str) -> None:
    parser = generate_workspace_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                subcmd,
                "--feature",
                "test",
                "--environment",
                "qa",
                "numpy",
            ]
        )


def test_workspace_parser_separates_manifest_and_export_paths() -> None:
    parser = generate_workspace_parser()

    parsed = parser.parse_args(
        ["--file", "pixi.toml", "export", "--file", "../environment.yml"]
    )

    assert parsed.manifest_file == Path("pixi.toml")
    assert parsed.output == Path("../environment.yml")


@pytest.mark.parametrize(
    "subcmd, module_attr, func_name",
    [
        ("init", "conda_workspaces.cli.workspace.init", "execute_init"),
        ("install", "conda_workspaces.cli.workspace.install", "execute_install"),
        ("lock", "conda_workspaces.cli.workspace.lock", "execute_lock"),
        ("attest", "conda_workspaces.cli.workspace.attest", "execute_attest"),
        ("verify", "conda_workspaces.cli.workspace.attest", "execute_verify"),
        ("sbom", "conda_workspaces.cli.workspace.sbom", "execute_sbom"),
        ("list", "conda_workspaces.cli.workspace.list", "execute_list"),
        ("info", "conda_workspaces.cli.workspace.info", "execute_info"),
        ("add", "conda_workspaces.cli.workspace.add", "execute_add"),
        ("update", "conda_workspaces.cli.workspace.update", "execute_update"),
        ("remove", "conda_workspaces.cli.workspace.remove", "execute_remove"),
        ("clean", "conda_workspaces.cli.workspace.clean", "execute_clean"),
        ("activate", "conda_workspaces.cli.workspace.activate", "execute_activate"),
        ("shell", "conda_workspaces.cli.workspace.shell", "execute_shell"),
    ],
    ids=[
        "init",
        "install",
        "lock",
        "attest",
        "verify",
        "sbom",
        "list",
        "info",
        "add",
        "update",
        "remove",
        "clean",
        "activate",
        "shell",
    ],
)
def test_workspace_dispatches_to_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    subcmd: str,
    module_attr: str,
    func_name: str,
) -> None:
    calls: list[str] = []

    def fake_handler(args):
        calls.append(subcmd)
        return 0

    mod = importlib.import_module(module_attr)
    monkeypatch.setattr(mod, func_name, fake_handler)

    args = argparse.Namespace(subcmd=subcmd)
    result = execute_workspace(args)
    assert result == 0
    assert calls == [subcmd]


@pytest.mark.parametrize(
    ("subcmd", "module_attr", "func_name", "payload"),
    [
        (
            "sbom",
            "conda_workspaces.cli.workspace.sbom",
            "execute_sbom",
            {
                "success": True,
                "format": "cyclonedx-json-v1.7",
                "environment": "default",
                "content": "{}\n",
            },
        ),
        (
            "attest",
            "conda_workspaces.cli.workspace.attest",
            "execute_attest",
            {
                "success": True,
                "sidecar": "/workspace/conda.lock.sigstore.json",
            },
        ),
        (
            "verify",
            "conda_workspaces.cli.workspace.attest",
            "execute_verify",
            {
                "success": True,
                "verified": True,
                "authorized": True,
                "sidecar": "/workspace/conda.lock.sigstore.json",
                "manifest": "/workspace/conda.toml",
                "lockfile": "/workspace/conda.lock",
                "predicate_type": "https://example.test/workspace/v1",
                "signer": {
                    "identity": "release@example.com",
                    "issuer": "https://issuer.example",
                    "timestamps": [],
                },
            },
        ),
    ],
    ids=["sbom", "attest", "verify"],
)
def test_workspace_data_commands_own_json_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    subcmd: str,
    module_attr: str,
    func_name: str,
    payload: dict[str, object],
) -> None:
    def emit_result(args: argparse.Namespace) -> int:
        assert args.json is True
        print(json.dumps(payload))
        return 0

    module = importlib.import_module(module_attr)
    monkeypatch.setattr(module, func_name, emit_result)

    result = execute_workspace(argparse.Namespace(subcmd=subcmd, json=True))

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == payload
    assert captured.err == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["attest", "--identity-token", "secret"],
        ["lock", "--sign", "--identity-token", "secret"],
        ["archive", "--sign", "--identity-token", "secret"],
    ],
    ids=["attest", "lock", "archive"],
)
def test_workspace_signing_rejects_identity_token_argument(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        generate_workspace_parser().parse_args(argv)


@pytest.mark.usefixtures("reset_conda_context")
@pytest.mark.parametrize(
    ("subcmd", "module_attr", "func_name"),
    [
        ("add", "conda_workspaces.cli.workspace.add", "execute_add"),
        ("update", "conda_workspaces.cli.workspace.update", "execute_update"),
        ("remove", "conda_workspaces.cli.workspace.remove", "execute_remove"),
        ("install", "conda_workspaces.cli.workspace.install", "execute_install"),
        ("lock", "conda_workspaces.cli.workspace.lock", "execute_lock"),
        ("clean", "conda_workspaces.cli.workspace.clean", "execute_clean"),
        (
            "import",
            "conda_workspaces.cli.workspace.import_manifest",
            "execute_import",
        ),
        ("archive", "conda_workspaces.cli.workspace.archive", "execute_archive"),
        (
            "unarchive",
            "conda_workspaces.cli.workspace.archive",
            "execute_unarchive",
        ),
    ],
    ids=[
        "add",
        "update",
        "remove",
        "install",
        "lock",
        "clean",
        "import",
        "archive",
        "unarchive",
    ],
)
def test_workspace_json_mutations_emit_single_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    subcmd: str,
    module_attr: str,
    func_name: str,
) -> None:
    calls: list[str] = []

    def fake_handler(args: argparse.Namespace) -> int:
        from conda.reporters import render

        calls.append(args.subcmd)
        Console().print("Rich status")
        print("plain status")
        render({"nested": True})
        return 0

    module = importlib.import_module(module_attr)
    monkeypatch.setattr(module, func_name, fake_handler)
    args = argparse.Namespace(subcmd=subcmd, json=True)
    reset_context(argparse_args=args)

    result = execute_workspace(args)

    captured = capsys.readouterr()
    assert result == 0
    assert calls == [subcmd]
    assert json.loads(captured.out) == {"success": True}
    assert captured.err == ""


@pytest.mark.usefixtures("reset_conda_context")
def test_workspace_json_dry_run_exit_is_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def dry_run(args: argparse.Namespace) -> int:
        del args
        print("preview")
        raise DryRunExit

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.add.execute_add",
        dry_run,
    )
    args = argparse.Namespace(subcmd="add", json=True)
    reset_context(argparse_args=args)

    result = execute_workspace(args)

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == {"success": True}
    assert captured.err == ""


@pytest.mark.usefixtures("reset_conda_context")
@pytest.mark.parametrize(
    "error_type",
    [CondaError, CondaSystemExit, RuntimeError],
    ids=["conda", "conda-system-exit", "unexpected"],
)
def test_workspace_json_errors_do_not_leak_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_type: type[Exception],
) -> None:
    def fail(args: argparse.Namespace) -> int:
        del args
        print("progress")
        raise error_type("failed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.add.execute_add",
        fail,
    )
    args = argparse.Namespace(subcmd="add", json=True)
    reset_context(argparse_args=args)

    with pytest.raises(error_type, match="failed"):
        execute_workspace(args)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.usefixtures("reset_conda_context")
def test_workspace_error_renders_publication_recovery_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recovery = Path("/workspace/.conda.lock.sigstore.json.recovery.rollback")

    def fail(args: argparse.Namespace) -> int:
        del args
        raise AttestationError(f"Publication failed. Recovery entry: {recovery}")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.attest.execute_attest",
        fail,
    )
    args = argparse.Namespace(subcmd="attest", json=False)
    reset_context(argparse_args=args)

    result = execute_workspace(args)

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert str(recovery) in captured.err


@pytest.mark.usefixtures("reset_conda_context")
def test_workspace_json_conda_error_redacts_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "JSON-LEAK"

    def fail(args: argparse.Namespace) -> int:
        del args
        raise CondaError(
            f"fetch failed for https://alice:{secret}@packages.test/t/OTHER/private"
        )

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.add.execute_add",
        fail,
    )
    args = argparse.Namespace(subcmd="add", json=True)
    reset_context(argparse_args=args)

    with pytest.raises(CondaError) as exc_info:
        execute_workspace(args)

    serialized = json.dumps(exc_info.value.dump_map())
    assert secret not in serialized
    assert "alice" not in serialized
    assert "OTHER" not in serialized
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.usefixtures("reset_conda_context")
def test_workspace_json_nonzero_result_routes_output_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(args: argparse.Namespace) -> int:
        del args
        print("failed")
        return 7

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.add.execute_add",
        fail,
    )
    args = argparse.Namespace(subcmd="add", json=True)
    reset_context(argparse_args=args)

    result = execute_workspace(args)

    captured = capsys.readouterr()
    assert result == 7
    assert captured.out == ""
    assert captured.err == "failed\n"


@pytest.mark.usefixtures("reset_conda_context")
def test_workspace_add_override_warning_preserves_json_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "json-warning"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
numpy = ">=2"

[target.linux-64.dependencies]
numpy = ">=2.1"
""",
        encoding="utf-8",
    )
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            str(path),
            "add",
            "--json",
            "--no-lockfile-update",
            "numpy=2.3",
        ]
    )
    reset_context(argparse_args=args)

    assert execute_workspace(args) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"success": True}
    assert "[target.linux-64.dependencies] overrides" in captured.err


def test_workspace_non_json_output_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def succeed(args: argparse.Namespace) -> int:
        del args
        print("status")
        return 0

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.add.execute_add",
        succeed,
    )

    result = execute_workspace(argparse.Namespace(subcmd="add", json=False))

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == "status\n"
    assert captured.err == ""


@pytest.mark.parametrize(
    "subcmd, module_attr, func_name",
    [
        ("run", "conda_workspaces.cli.task.run", "execute_run"),
        ("list", "conda_workspaces.cli.task.list", "execute_list"),
        ("add", "conda_workspaces.cli.task.add", "execute_add"),
        ("remove", "conda_workspaces.cli.task.remove", "execute_remove"),
        ("export", "conda_workspaces.cli.task.export", "execute_export"),
    ],
    ids=["run", "list", "add", "remove", "export"],
)
def test_task_dispatches_to_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    subcmd: str,
    module_attr: str,
    func_name: str,
) -> None:
    calls: list[str] = []

    def fake_handler(args):
        calls.append(subcmd)
        return 0

    mod = importlib.import_module(module_attr)
    monkeypatch.setattr(mod, func_name, fake_handler)

    args = argparse.Namespace(subcmd=subcmd)
    result = execute_task(args)
    assert result == 0
    assert calls == [subcmd]


def test_shell_accepts_environment_flag() -> None:
    parser = generate_workspace_parser()
    parsed = parser.parse_args(["shell", "-e", "test"])
    assert parsed.environment == "test"


@pytest.mark.parametrize(
    "argv",
    [
        ["init", "--json"],
        ["activate", "--json"],
        ["run", "--json", "--", "echo", "hi"],
        ["shell", "--json"],
    ],
    ids=["init", "activate", "run", "shell"],
)
def test_side_effect_subcommands_accept_json_silently(argv: list[str]) -> None:
    """Side-effect subcommands must tolerate ``--json`` without argparse errors.

    These subcommands register ``--json`` with ``help=SUPPRESS`` via
    :func:`_accept_json_silently` because they have no structured output
    to emit, but CI wrappers still pass ``--json`` globally; crashing
    with ``unrecognized arguments: --json`` is the wrong UX. See the
    ``--json contract`` section in ``AGENTS.md``.
    """
    parser = generate_workspace_parser()
    parsed = parser.parse_args(argv)
    assert parsed.subcmd == argv[0]


@pytest.mark.parametrize(
    "args, expected_attr, expected_value",
    [
        (["run", "test"], "task_name", "test"),
        (["run", "-e", "dev", "test"], "environment", "dev"),
        (["run", "--skip-deps", "test"], "skip_deps", True),
        (["run", "--templated", "test"], "templated", True),
        (["run", "--clean-env", "test"], "clean_env", True),
    ],
    ids=[
        "run-task-name",
        "run-environment",
        "run-skip-deps",
        "run-templated",
        "run-clean-env",
    ],
)
def test_task_parser_args(
    args: list[str], expected_attr: str, expected_value: object
) -> None:
    parser = generate_task_parser()
    parsed = parser.parse_args(args)
    assert getattr(parsed, expected_attr) == expected_value


def test_archive_subparser_registered() -> None:
    parser = generate_workspace_parser()
    ns = parser.parse_args(["archive", "-o", "out.tar.gz"])
    assert ns.subcmd == "archive"
    assert str(ns.output) == "out.tar.gz"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["archive", "-o", "out.tar.gz", "--receipt"], True),
        (["archive", "-o", "out.tar.gz", "--receipt", "r.json"], Path("r.json")),
    ],
    ids=["default", "path"],
)
def test_archive_subparser_receipt(args: list[str], expected: object) -> None:
    parser = generate_workspace_parser()
    ns = parser.parse_args(args)
    assert ns.receipt == expected


def test_unarchive_subparser_registered() -> None:
    parser = generate_workspace_parser()
    ns = parser.parse_args(
        [
            "unarchive",
            "project.tar.gz",
            "--install",
            "-e",
            "runtime",
            "--prefix",
            "/opt/runtime",
            "--dest",
            "/tmp/rootfs",
        ]
    )
    assert ns.subcmd == "unarchive"
    assert str(ns.archive_path) == "project.tar.gz"
    assert ns.install is True
    assert ns.environment == "runtime"
    assert ns.prefix == "/opt/runtime"
    assert ns.dest == Path("/tmp/rootfs")


def test_unarchive_subparser_receipt() -> None:
    parser = generate_workspace_parser()
    ns = parser.parse_args(
        [
            "unarchive",
            "project.tar.gz",
            "--receipt",
            "project.receipt.json",
            "--require-sha256",
        ]
    )

    assert ns.receipt == Path("project.receipt.json")
    assert ns.require_sha256 is True
