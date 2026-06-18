"""Tests for conda_workspaces.cli.workspace.attest."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from conda_workspaces.cli.workspace.attest import execute_attest, execute_verify

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from rich.console import Console

_ATTEST_DEFAULTS = {
    "file": None,
    "attestation": None,
    "identity_token": None,
    "json": False,
}

_VERIFY_DEFAULTS = {
    "file": None,
    "attestation": None,
    "cert_identity": None,
    "cert_oidc_issuer": None,
    "json": False,
}


def test_execute_attest_writes_attestation(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    attestation = pixi_workspace / "custom.sigstore.json"
    calls: list[dict[str, object]] = []

    def fake_write_workspace_attestation(**kwargs):
        calls.append(kwargs)
        return attestation

    monkeypatch.setattr(
        "conda_workspaces.attestations.write_workspace_attestation",
        fake_write_workspace_attestation,
    )

    result = execute_attest(
        make_args(
            _ATTEST_DEFAULTS,
            attestation=attestation,
            identity_token="token",
        ),
        console=rich_console,
    )

    assert result == 0
    assert calls == [
        {
            "root": pixi_workspace,
            "manifest_path": pixi_workspace / "pixi.toml",
            "lockfile_path": pixi_workspace / "conda.lock",
            "bundle_path": attestation,
            "identity_token": "token",
        }
    ]
    assert "Signed" in rich_console.file.getvalue()


def test_execute_attest_json_suppresses_status(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    monkeypatch.setattr(
        "conda_workspaces.attestations.write_workspace_attestation",
        lambda **kwargs: pixi_workspace / "conda.lock.sigstore.json",
    )

    result = execute_attest(
        make_args(_ATTEST_DEFAULTS, json=True),
        console=rich_console,
    )

    assert result == 0
    assert rich_console.file.getvalue() == ""


def test_execute_verify_checks_attestation(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    attestation = pixi_workspace / "custom.sigstore.json"
    calls: list[dict[str, object]] = []

    def fake_verify_workspace_attestation(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "conda_workspaces.attestations.verify_workspace_attestation",
        fake_verify_workspace_attestation,
    )

    result = execute_verify(
        make_args(
            _VERIFY_DEFAULTS,
            attestation=attestation,
            cert_identity="user@example.com",
            cert_oidc_issuer="https://issuer.example",
        ),
        console=rich_console,
    )

    assert result == 0
    assert len(calls) == 1
    assert calls[0]["root"] == pixi_workspace
    assert calls[0]["manifest_path"] == pixi_workspace / "pixi.toml"
    assert calls[0]["lockfile_path"] == pixi_workspace / "conda.lock"
    assert calls[0]["bundle_path"] == attestation
    identities = calls[0]["identities"]
    assert identities[0].identity == "user@example.com"
    assert identities[0].issuer == "https://issuer.example"
    assert "Verified" in rich_console.file.getvalue()


def test_execute_verify_json_outputs_payload(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    calls: list[dict[str, object]] = []

    def fake_verify_workspace_attestation(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "conda_workspaces.attestations.verify_workspace_attestation",
        fake_verify_workspace_attestation,
    )

    result = execute_verify(
        make_args(
            _VERIFY_DEFAULTS,
            cert_identity="user@example.com",
            cert_oidc_issuer="https://issuer.example",
            json=True,
        ),
        console=rich_console,
    )

    assert result == 0
    assert len(calls) == 1
    payload = json.loads(rich_console.file.getvalue())
    assert payload == {
        "verified": True,
        "manifest": str(pixi_workspace / "pixi.toml"),
        "lockfile": str(pixi_workspace / "conda.lock"),
        "attestation": str(pixi_workspace / "conda.lock.sigstore.json"),
    }
