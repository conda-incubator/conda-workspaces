"""Tests for workspace Sigstore attestations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.attestations import (
    IN_TOTO_PAYLOAD_TYPE,
    WORKSPACE_ATTESTATION_PREDICATE_TYPE,
    TrustIdentity,
    WorkspaceAttestation,
    default_attestation_path,
    trust_identities_from_cli,
    verify_workspace_attestation,
    write_workspace_attestation,
)
from conda_workspaces.exceptions import AttestationError

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


@pytest.fixture
def attested_workspace(tmp_path: Path) -> Path:
    (tmp_path / "conda.toml").write_text(
        """\
[workspace]
name = "attested"
channels = ["conda-forge"]
platforms = ["linux-64"]
""",
        encoding="utf-8",
    )
    (tmp_path / "conda.lock").write_text(
        "version: 1\nenvironments: {}\npackages: []\n",
        encoding="utf-8",
    )
    return tmp_path


def copied_statement(attestation: WorkspaceAttestation) -> dict[str, Any]:
    return json.loads(json.dumps(attestation.statement))


def test_workspace_attestation_statement(attested_workspace: Path) -> None:
    attestation = WorkspaceAttestation.build(
        root=attested_workspace,
        manifest_path=attested_workspace / "conda.toml",
        lockfile_path=attested_workspace / "conda.lock",
    )

    assert attestation.statement["_type"] == "https://in-toto.io/Statement/v1"
    assert (
        attestation.statement["predicateType"] == WORKSPACE_ATTESTATION_PREDICATE_TYPE
    )
    assert attestation.workspace_paths == ("conda.toml", "conda.lock")
    assert attestation.predicate["lockfile"] == {"format": "conda-workspaces-lock-v1"}
    assert set(attestation.subject_digests) == {"conda.toml", "conda.lock"}


def test_write_workspace_attestation_uses_default_sidecar(
    attested_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[bytes] = []

    def fake_signer(payload: bytes, identity_token: str | None) -> str:
        payloads.append(payload)
        assert identity_token == "token"
        return '{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}'

    monkeypatch.setattr(
        "conda_workspaces.attestations.sign_payload_with_sigstore",
        fake_signer,
    )
    path = write_workspace_attestation(
        root=attested_workspace,
        manifest_path=attested_workspace / "conda.toml",
        lockfile_path=attested_workspace / "conda.lock",
        identity_token="token",
    )

    assert path == attested_workspace / "conda.lock.sigstore.json"
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert (
        json.loads(payloads[0])["predicateType"] == WORKSPACE_ATTESTATION_PREDICATE_TYPE
    )


def test_verify_workspace_attestation_checks_identity_and_file_digests(
    attested_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = WorkspaceAttestation.build(
        root=attested_workspace,
        manifest_path=attested_workspace / "conda.toml",
        lockfile_path=attested_workspace / "conda.lock",
    )
    default_attestation_path(attested_workspace / "conda.lock").write_text(
        '{"bundle":true}\n',
        encoding="utf-8",
    )
    identities = (
        TrustIdentity(
            identity=(
                "https://github.com/conda-incubator/conda-workspaces/"
                ".github/workflows/release.yml@refs/heads/main"
            ),
            issuer="https://token.actions.githubusercontent.com",
        ),
    )
    seen_identities: list[tuple[TrustIdentity, ...]] = []

    def fake_verifier(
        bundle_json: str,
        trusted: tuple[TrustIdentity, ...],
    ) -> tuple[str, bytes]:
        assert json.loads(bundle_json) == {"bundle": True}
        seen_identities.append(trusted)
        return IN_TOTO_PAYLOAD_TYPE, attestation.payload()

    monkeypatch.setattr(
        "conda_workspaces.attestations.verify_sigstore_bundle",
        fake_verifier,
    )
    result = verify_workspace_attestation(
        root=attested_workspace,
        identities=identities,
    )

    assert result.workspace_paths == ("conda.toml", "conda.lock")
    assert seen_identities == [identities]


def test_verify_workspace_attestation_detects_tampered_lockfile(
    attested_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = WorkspaceAttestation.build(
        root=attested_workspace,
        manifest_path=attested_workspace / "conda.toml",
        lockfile_path=attested_workspace / "conda.lock",
    )
    default_attestation_path(attested_workspace / "conda.lock").write_text(
        '{"bundle":true}\n',
        encoding="utf-8",
    )
    (attested_workspace / "conda.lock").write_text(
        "version: 1\nenvironments: {}\npackages:\n  - changed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "conda_workspaces.attestations.verify_sigstore_bundle",
        lambda bundle, identities: (IN_TOTO_PAYLOAD_TYPE, attestation.payload()),
    )

    with pytest.raises(AttestationError, match="digest mismatch"):
        verify_workspace_attestation(
            root=attested_workspace,
            identities=(TrustIdentity(identity="user@example.com", issuer="issuer"),),
        )


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (
            lambda statement: statement.__setitem__("_type", "wrong"),
            "unsupported in-toto statement type",
        ),
        (
            lambda statement: statement.__setitem__("predicateType", "wrong"),
            "unsupported predicate type",
        ),
        (
            lambda statement: statement["predicate"].__setitem__("version", 2),
            "unsupported format version",
        ),
        (
            lambda statement: statement["predicate"]["workspace"].__setitem__(
                "lockfile", "../conda.lock"
            ),
            "Invalid attested workspace lockfile path",
        ),
    ],
    ids=["type", "predicate-type", "version", "unsafe-path"],
)
def test_workspace_attestation_rejects_invalid_statements(
    attested_workspace: Path,
    mutation,
    expected: str,
) -> None:
    statement = copied_statement(
        WorkspaceAttestation.build(
            root=attested_workspace,
            manifest_path=attested_workspace / "conda.toml",
            lockfile_path=attested_workspace / "conda.lock",
        )
    )
    mutation(statement)

    with pytest.raises(AttestationError, match=expected):
        WorkspaceAttestation(statement).validate()


def test_workspace_attestation_load_rejects_duplicate_keys() -> None:
    with pytest.raises(AttestationError, match="duplicate JSON key"):
        WorkspaceAttestation.load_payload(
            b'{"_type":"https://in-toto.io/Statement/v1","_type":"x"}'
        )


def test_verify_workspace_attestation_requires_trusted_identity(
    attested_workspace: Path,
) -> None:
    default_attestation_path(attested_workspace / "conda.lock").write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(AttestationError, match="No trusted Sigstore identity"):
        verify_workspace_attestation(root=attested_workspace, identities=())


@pytest.mark.parametrize(
    ("identity", "issuer", "expected"),
    [
        ("user@example.com", "https://issuer.example", 1),
        (None, None, 0),
    ],
    ids=["configured", "empty"],
)
def test_trust_identities_from_cli(
    identity: str | None,
    issuer: str | None,
    expected: int,
) -> None:
    identities = trust_identities_from_cli(identity, issuer)

    assert len(identities) == expected


def test_trust_identities_from_cli_requires_pair() -> None:
    with pytest.raises(AttestationError, match="must be passed together"):
        trust_identities_from_cli("user@example.com", None)
