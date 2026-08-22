"""Tests for ``conda workspace attest`` and ``verify``."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest
from conda_sigstore.evidence import SignerIdentity
from conda_sigstore.verification import VerifiedStatement

import conda_workspaces.cli.workspace.attest as attest_module
from conda_workspaces.attestations import (
    MAX_ATTESTATION_BYTES,
    WORKSPACE_ATTESTATION_PREDICATE_TYPE,
    AttestationOutput,
    SignerPolicy,
    WorkspaceAttestation,
    WorkspaceVerification,
)
from conda_workspaces.cli.workspace.attest import execute_attest, execute_verify
from conda_workspaces.exceptions import AttestationError, CondaWorkspacesError

from ..conftest import make_args

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from rich.console import Console

    from conda_workspaces.publication import WorkspaceSnapshot


_DEFAULTS = {
    "manifest_file": None,
    "attestation": None,
    "cert_identity": None,
    "cert_oidc_issuer": None,
    "dry_run": False,
    "json": False,
}

_LOCKFILE_BYTES = b"version: 1\nenvironments: {}\npackages: []\n"
_BUNDLE_BYTES = b'{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}\n'
_SIGNER = SignerIdentity(
    identity="release@example.com",
    issuer="https://issuer.example",
)
_SIGNER_POLICY = SignerPolicy(
    identity=_SIGNER.identity,
    issuer=_SIGNER.issuer,
)


@pytest.fixture
def attestation_workspace(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create a workspace with a canonical lockfile and make it current."""
    (pixi_workspace / "conda.lock").write_bytes(_LOCKFILE_BYTES)
    monkeypatch.chdir(pixi_workspace)
    return pixi_workspace


def workspace_verification(
    snapshot: WorkspaceSnapshot,
    signer_policy: SignerPolicy | None,
) -> WorkspaceVerification:
    """Return authenticated evidence bound to *snapshot*."""
    attestation = WorkspaceAttestation.from_snapshot(snapshot)
    statement = attestation.to_statement()
    return WorkspaceVerification(
        attestation=attestation,
        evidence=VerifiedStatement(
            statement=statement,
            payload=statement.payload(),
            signer=_SIGNER,
            timestamps=("2026-08-22T09:10:11Z", "2026-08-22T09:10:12Z"),
        ),
        authorized=(
            None if signer_policy is None else signer_policy.authorize(_SIGNER)
        ),
    )


@pytest.mark.parametrize("json_output", [False, True], ids=["human", "json"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["sign", "dry-run"])
def test_execute_attest_reports_one_result(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    json_output: bool,
    dry_run: bool,
) -> None:
    sidecar = attestation_workspace / "release.sigstore.json"
    signed: list[WorkspaceSnapshot] = []

    def sign(snapshot: WorkspaceSnapshot) -> str:
        signed.append(snapshot)
        return _BUNDLE_BYTES.decode("utf-8")

    monkeypatch.setattr(attest_module, "sign_workspace_snapshot", sign)

    result = execute_attest(
        make_args(
            _DEFAULTS,
            attestation=sidecar,
            dry_run=dry_run,
            json=json_output,
        ),
        console=rich_console,
    )

    assert result == 0
    assert sidecar.exists() is not dry_run
    if dry_run:
        assert signed == []
    else:
        assert len(signed) == 1
        assert signed[0].manifest_path == attestation_workspace / "pixi.toml"
        assert (
            signed[0].manifest_bytes
            == (attestation_workspace / "pixi.toml").read_bytes()
        )
        assert signed[0].manifest_format == "pixi-toml"
        assert signed[0].lockfile_path == attestation_workspace / "conda.lock"
        assert signed[0].lockfile_bytes == _LOCKFILE_BYTES
        assert sidecar.read_bytes() == _BUNDLE_BYTES

    output = rich_console.file.getvalue()
    if json_output:
        assert json.loads(output) == {
            "success": True,
            "sidecar": str(sidecar),
        }
    else:
        action = "Would sign" if dry_run else "Signed"
        assert output == f"{action} workspace to {sidecar}\n"


@pytest.mark.parametrize(
    "unsafe_output",
    ["manifest", "lockfile", "hardlink", "symlink", "symlink-parent"],
    ids=["manifest", "lockfile", "hardlink", "symlink", "symlink-parent"],
)
def test_execute_attest_dry_run_rejects_unsafe_output(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_output: str,
) -> None:
    manifest = attestation_workspace / "pixi.toml"
    lockfile = attestation_workspace / "conda.lock"
    sidecar = attestation_workspace / "release.sigstore.json"

    if unsafe_output == "manifest":
        sidecar = manifest
    elif unsafe_output == "lockfile":
        sidecar = lockfile
    elif unsafe_output == "hardlink":
        os.link(lockfile, sidecar)
    elif unsafe_output == "symlink":
        sidecar.symlink_to(attestation_workspace / "elsewhere.json")
    else:
        target = attestation_workspace / "real-output"
        target.mkdir()
        linked_parent = attestation_workspace / "linked-output"
        linked_parent.symlink_to(target, target_is_directory=True)
        sidecar = linked_parent / "release.sigstore.json"

    before_manifest = manifest.read_bytes()
    before_lockfile = lockfile.read_bytes()

    def unexpected_sign(snapshot: WorkspaceSnapshot) -> str:
        raise AssertionError(f"dry-run attempted to sign {snapshot}")

    monkeypatch.setattr(attest_module, "sign_workspace_snapshot", unexpected_sign)

    with pytest.raises(AttestationError, match="Attestation output"):
        execute_attest(
            make_args(
                _DEFAULTS,
                attestation=sidecar,
                dry_run=True,
            )
        )

    assert manifest.read_bytes() == before_manifest
    assert lockfile.read_bytes() == before_lockfile


def test_execute_attest_rejects_output_created_during_signing(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = attestation_workspace / "release.sigstore.json"
    attacker_content = b"untrusted bundle\n"

    def race_output(snapshot: WorkspaceSnapshot) -> str:
        del snapshot
        sidecar.write_bytes(attacker_content)
        return _BUNDLE_BYTES.decode("utf-8")

    monkeypatch.setattr(attest_module, "sign_workspace_snapshot", race_output)

    with pytest.raises(AttestationError, match="changed before publication"):
        execute_attest(make_args(_DEFAULTS, attestation=sidecar))

    assert sidecar.read_bytes() == attacker_content


@pytest.mark.parametrize(
    "changed_input",
    ["manifest", "lockfile"],
    ids=["manifest", "lockfile"],
)
def test_execute_attest_rejects_input_changed_during_signing(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
) -> None:
    sidecar = attestation_workspace / "release.sigstore.json"
    path = attestation_workspace / (
        "pixi.toml" if changed_input == "manifest" else "conda.lock"
    )

    def race_input(snapshot: WorkspaceSnapshot) -> str:
        del snapshot
        content = path.read_bytes()
        path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
        return _BUNDLE_BYTES.decode("utf-8")

    monkeypatch.setattr(attest_module, "sign_workspace_snapshot", race_input)

    with pytest.raises(CondaWorkspacesError, match=f"{changed_input} changed"):
        execute_attest(make_args(_DEFAULTS, attestation=sidecar))

    assert not sidecar.exists()


@pytest.mark.parametrize(
    "previous_sidecar",
    [_BUNDLE_BYTES, None],
    ids=["existing-sidecar", "missing-sidecar"],
)
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("input-change", "manifest changed"),
        ("writer-after-publication", "changed before publication"),
    ],
    ids=["input-change", "writer-after-publication"],
)
def test_execute_attest_restores_sidecar_after_publication_failure(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_sidecar: bytes | None,
    failure: str,
    message: str,
    fail_attestation_writer_after_publication: Callable[[], None],
) -> None:
    manifest = attestation_workspace / "pixi.toml"
    sidecar = attestation_workspace / "release.sigstore.json"
    if previous_sidecar is not None:
        sidecar.write_bytes(previous_sidecar)
    original_write = AttestationOutput.write

    def write_then_change_manifest(
        output: AttestationOutput,
        bundle_json: str,
    ) -> Path:
        written = original_write(output, bundle_json)
        manifest.write_text("[workspace]\nname = 'concurrent'\n", encoding="utf-8")
        return written

    monkeypatch.setattr(
        attest_module,
        "sign_workspace_snapshot",
        lambda snapshot: '{"bundle":true}',
    )
    if failure == "input-change":
        monkeypatch.setattr(AttestationOutput, "write", write_then_change_manifest)
    else:
        fail_attestation_writer_after_publication()

    with pytest.raises(CondaWorkspacesError, match=message):
        execute_attest(make_args(_DEFAULTS, attestation=sidecar))

    if previous_sidecar is None:
        assert not sidecar.exists()
    else:
        assert sidecar.read_bytes() == previous_sidecar


@pytest.mark.parametrize("json_output", [False, True], ids=["human", "json"])
@pytest.mark.parametrize(
    "authorize",
    [False, True],
    ids=["authenticate-only", "authorized"],
)
def test_execute_verify_reports_authenticated_evidence(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    json_output: bool,
    authorize: bool,
) -> None:
    sidecar = attestation_workspace / "release.sigstore.json"
    sidecar.write_bytes(_BUNDLE_BYTES)
    calls: list[tuple[bytes, WorkspaceSnapshot, SignerPolicy | None]] = []

    def verify(
        bundle_bytes: bytes,
        snapshot: WorkspaceSnapshot,
        signer_policy: SignerPolicy | None,
    ) -> WorkspaceVerification:
        calls.append((bundle_bytes, snapshot, signer_policy))
        return workspace_verification(snapshot, signer_policy)

    monkeypatch.setattr(attest_module, "verify_workspace_snapshot", verify)
    signer_args = (
        {
            "cert_identity": _SIGNER.identity,
            "cert_oidc_issuer": _SIGNER.issuer,
        }
        if authorize
        else {}
    )

    result = execute_verify(
        make_args(
            _DEFAULTS,
            attestation=sidecar,
            json=json_output,
            **signer_args,
        ),
        console=rich_console,
    )

    assert result == 0
    assert len(calls) == 1
    bundle_bytes, snapshot, signer_policy = calls[0]
    assert bundle_bytes == _BUNDLE_BYTES
    assert snapshot.manifest_path == attestation_workspace / "pixi.toml"
    assert snapshot.manifest_bytes == (attestation_workspace / "pixi.toml").read_bytes()
    assert snapshot.manifest_format == "pixi-toml"
    assert snapshot.lockfile_path == attestation_workspace / "conda.lock"
    assert snapshot.lockfile_bytes == _LOCKFILE_BYTES
    assert signer_policy == (_SIGNER_POLICY if authorize else None)

    output = rich_console.file.getvalue()
    if json_output:
        assert json.loads(output) == {
            "success": True,
            "verified": True,
            "authorized": True if authorize else None,
            "sidecar": str(sidecar),
            "manifest": str(attestation_workspace / "pixi.toml"),
            "lockfile": str(attestation_workspace / "conda.lock"),
            "predicate_type": WORKSPACE_ATTESTATION_PREDICATE_TYPE,
            "signer": {
                "identity": _SIGNER.identity,
                "issuer": _SIGNER.issuer,
                "timestamps": [
                    "2026-08-22T09:10:11Z",
                    "2026-08-22T09:10:12Z",
                ],
            },
        }
    else:
        authorization = "authorized" if authorize else "authorization not evaluated"
        assert output == (
            f"Verified workspace attestation from {_SIGNER.identity} "
            f"({authorization})\n"
        )


@pytest.mark.parametrize(
    ("identity", "issuer"),
    [
        (_SIGNER.identity, None),
        (None, _SIGNER.issuer),
    ],
    ids=["missing-issuer", "missing-identity"],
)
def test_execute_verify_requires_paired_signer_policy(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity: str | None,
    issuer: str | None,
) -> None:
    def unexpected_verify(*args: object, **kwargs: object) -> WorkspaceVerification:
        raise AssertionError(f"unexpected verification: {args}, {kwargs}")

    monkeypatch.setattr(attest_module, "verify_workspace_snapshot", unexpected_verify)

    with pytest.raises(AttestationError, match="must be supplied together"):
        execute_verify(
            make_args(
                _DEFAULTS,
                cert_identity=identity,
                cert_oidc_issuer=issuer,
            )
        )


def test_execute_verify_enforces_expected_signer(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = attestation_workspace / "release.sigstore.json"
    sidecar.write_bytes(_BUNDLE_BYTES)

    def reject_signer(
        bundle_bytes: bytes,
        snapshot: WorkspaceSnapshot,
        signer_policy: SignerPolicy | None,
    ) -> WorkspaceVerification:
        del bundle_bytes, signer_policy
        verification = workspace_verification(snapshot, None)
        return WorkspaceVerification(
            attestation=verification.attestation,
            evidence=verification.evidence,
            authorized=False,
        )

    monkeypatch.setattr(attest_module, "verify_workspace_snapshot", reject_signer)

    with pytest.raises(AttestationError, match="does not match"):
        execute_verify(
            make_args(
                _DEFAULTS,
                attestation=sidecar,
                cert_identity=_SIGNER.identity,
                cert_oidc_issuer=_SIGNER.issuer,
            )
        )


@pytest.mark.parametrize(
    "changed_input",
    ["manifest", "lockfile"],
    ids=["manifest", "lockfile"],
)
def test_execute_verify_rejects_input_changed_during_verification(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    changed_input: str,
) -> None:
    sidecar = attestation_workspace / "release.sigstore.json"
    sidecar.write_bytes(_BUNDLE_BYTES)
    path = attestation_workspace / (
        "pixi.toml" if changed_input == "manifest" else "conda.lock"
    )

    def race_input(
        bundle_bytes: bytes,
        snapshot: WorkspaceSnapshot,
        signer_policy: SignerPolicy | None,
    ) -> WorkspaceVerification:
        del bundle_bytes
        content = path.read_bytes()
        path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
        return workspace_verification(snapshot, signer_policy)

    monkeypatch.setattr(attest_module, "verify_workspace_snapshot", race_input)

    with pytest.raises(CondaWorkspacesError, match=f"{changed_input} changed"):
        execute_verify(
            make_args(_DEFAULTS, attestation=sidecar, json=True),
            console=rich_console,
        )

    assert rich_console.file.getvalue() == ""


@pytest.mark.parametrize(
    "unsafe_sidecar",
    ["oversized", "symlink"],
    ids=["oversized", "symlink"],
)
def test_execute_verify_rejects_unsafe_sidecar_before_cryptography(
    attestation_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_sidecar: str,
) -> None:
    sidecar = attestation_workspace / "release.sigstore.json"
    if unsafe_sidecar == "oversized":
        sidecar.write_bytes(b"x" * (MAX_ATTESTATION_BYTES + 1))
    else:
        target = attestation_workspace / "bundle-target.json"
        target.write_bytes(_BUNDLE_BYTES)
        sidecar.symlink_to(target)

    def unexpected_verify(*args: object, **kwargs: object) -> WorkspaceVerification:
        raise AssertionError(f"unexpected verification: {args}, {kwargs}")

    monkeypatch.setattr(attest_module, "verify_workspace_snapshot", unexpected_verify)

    with pytest.raises(AttestationError, match="Cannot read Sigstore bundle safely"):
        execute_verify(make_args(_DEFAULTS, attestation=sidecar))
