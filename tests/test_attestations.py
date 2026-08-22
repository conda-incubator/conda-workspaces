"""Tests for workspace-specific Sigstore attestations."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from conda_sigstore.evidence import SignerIdentity
from conda_sigstore.statements import InTotoStatement
from conda_sigstore.verification import VerifiedStatement

import conda_workspaces.attestations as attestations_mod
import conda_workspaces.paths as paths_mod
from conda_workspaces.attestations import (
    WORKSPACE_ATTESTATION_PREDICATE_TYPE,
    AttestationOutput,
    SignerPolicy,
    WorkspaceAttestation,
    read_attestation_bundle,
    sign_attestation_payload,
    sign_workspace_snapshot,
    verify_attestation_bundle,
    verify_workspace_snapshot,
)
from conda_workspaces.exceptions import AttestationError
from conda_workspaces.publication import WorkspaceSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_MANIFEST = b'[workspace]\nname = "example"\n'
_LOCKFILE = b"version: 1\nenvironments: {}\npackages: []\n"
_SIGNER = SignerIdentity(
    identity="signer@example.com",
    issuer="https://issuer.example",
)


@pytest.fixture
def workspace_statement() -> WorkspaceAttestation:
    """Return one valid strict workspace statement model."""
    return WorkspaceAttestation.build(
        manifest_path="conda.toml",
        manifest_bytes=_MANIFEST,
        manifest_format="conda-toml",
        lockfile_path="conda.lock",
        lockfile_bytes=_LOCKFILE,
    )


@pytest.fixture
def workspace_snapshot(tmp_path: Path) -> WorkspaceSnapshot:
    """Return exact workspace inputs suitable for signing and verification."""
    return WorkspaceSnapshot.from_bytes(
        root=tmp_path,
        manifest_path=tmp_path / "conda.toml",
        manifest_bytes=_MANIFEST,
        manifest_format="conda-toml",
        lockfile_path=tmp_path / "conda.lock",
        lockfile_bytes=_LOCKFILE,
    )


@pytest.mark.parametrize(
    ("manifest_path", "manifest_format"),
    [
        ("conda.toml", "conda-toml"),
        ("pixi.toml", "pixi-toml"),
        ("pyproject.toml", "pyproject-toml"),
    ],
    ids=["conda", "pixi", "pyproject"],
)
def test_workspace_attestation_round_trip(
    manifest_path: str,
    manifest_format: str,
) -> None:
    attestation = WorkspaceAttestation.build(
        manifest_path=manifest_path,
        manifest_bytes=_MANIFEST,
        manifest_format=manifest_format,
        lockfile_path="conda.lock",
        lockfile_bytes=_LOCKFILE,
    )

    parsed = WorkspaceAttestation.from_payload(attestation.payload())

    assert parsed == attestation
    assert parsed.to_statement().predicate_type == WORKSPACE_ATTESTATION_PREDICATE_TYPE
    parsed.verify_content(
        manifest_path=manifest_path,
        manifest_bytes=_MANIFEST,
        manifest_format=manifest_format,
        lockfile_path="conda.lock",
        lockfile_bytes=_LOCKFILE,
    )


@pytest.mark.parametrize(
    ("manifest_path", "manifest_format", "lockfile_path", "message"),
    [
        (
            "nested/conda.toml",
            "conda-toml",
            "conda.lock",
            "manifest path does not match",
        ),
        (
            "conda.toml",
            "pixi-toml",
            "conda.lock",
            "manifest path does not match",
        ),
        (
            "conda.toml",
            "conda-toml",
            "nested/conda.lock",
            "canonical conda.lock",
        ),
    ],
    ids=["nested-manifest", "format-mismatch", "nested-lockfile"],
)
def test_workspace_attestation_requires_canonical_subject_metadata(
    manifest_path: str,
    manifest_format: str,
    lockfile_path: str,
    message: str,
) -> None:
    with pytest.raises(AttestationError, match=message):
        WorkspaceAttestation.build(
            manifest_path=manifest_path,
            manifest_bytes=_MANIFEST,
            manifest_format=manifest_format,
            lockfile_path=lockfile_path,
            lockfile_bytes=_LOCKFILE,
        )


def _statement_value(attestation: WorkspaceAttestation) -> dict[str, object]:
    return deepcopy(dict(attestation.to_statement().value))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(
            lambda value: value.update({"extra": True}),
            "unsupported fields",
            id="statement-extra",
        ),
        pytest.param(
            lambda value: value.update({"predicateType": "https://invalid.example"}),
            "predicate type",
            id="predicate-type",
        ),
        pytest.param(
            lambda value: value.update({"subject": value["subject"][:1]}),
            "exactly two",
            id="subject-count",
        ),
        pytest.param(
            lambda value: value["subject"][0].update({"extra": True}),
            "only name and digest",
            id="subject-extra",
        ),
        pytest.param(
            lambda value: value["subject"][0]["digest"].update({"sha512": "a"}),
            "only sha256",
            id="digest-extra",
        ),
        pytest.param(
            lambda value: value["subject"][0]["digest"].update({"sha256": "A" * 64}),
            "lowercase",
            id="uppercase-digest",
        ),
        pytest.param(
            lambda value: value["predicate"].update({"version": 2}),
            "version",
            id="version",
        ),
        pytest.param(
            lambda value: value["predicate"]["workspace"].update(
                {"manifest": "different.toml"}
            ),
            "subjects do not match",
            id="path-binding",
        ),
        pytest.param(
            lambda value: value["predicate"]["manifest"].update({"format": "unknown"}),
            "manifest format",
            id="manifest-format",
        ),
        pytest.param(
            lambda value: value["predicate"]["lockfile"].update(
                {"format": "rattler-lock-v6"}
            ),
            "lockfile format",
            id="lockfile-format",
        ),
    ],
)
def test_workspace_attestation_rejects_invalid_fields(
    workspace_statement: WorkspaceAttestation,
    mutate: Callable[[dict[str, object]], None],
    message: str,
) -> None:
    value = _statement_value(workspace_statement)
    mutate(value)

    with pytest.raises(AttestationError, match=message):
        WorkspaceAttestation.from_payload(json.dumps(value))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"manifest_bytes": b"changed"}, "manifest bytes"),
        ({"lockfile_bytes": b"changed"}, "lockfile bytes"),
        ({"manifest_path": "pixi.toml"}, "paths or manifest format"),
        ({"manifest_format": "pixi-toml"}, "paths or manifest format"),
        ({"lockfile_path": "nested/conda.lock"}, "paths or manifest format"),
    ],
)
def test_workspace_attestation_binds_exact_content_and_metadata(
    workspace_statement: WorkspaceAttestation,
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "manifest_path": "conda.toml",
        "manifest_bytes": _MANIFEST,
        "manifest_format": "conda-toml",
        "lockfile_path": "conda.lock",
        "lockfile_bytes": _LOCKFILE,
    }
    values.update(changes)

    with pytest.raises(AttestationError, match=message):
        workspace_statement.verify_content(**values)


@pytest.mark.parametrize(
    ("identity", "issuer", "message"),
    [
        ("signer@example.com", None, "supplied together"),
        (None, "https://issuer.example", "supplied together"),
        ("", "https://issuer.example", "identity cannot be empty"),
        ("signer@example.com", "", "issuer cannot be empty"),
    ],
)
def test_signer_policy_requires_complete_nonempty_values(
    identity: str | None,
    issuer: str | None,
    message: str,
) -> None:
    with pytest.raises(AttestationError, match=message):
        SignerPolicy.from_values(identity, issuer)


def test_signer_policy_is_optional_when_both_values_are_omitted() -> None:
    assert SignerPolicy.from_values(None, None) is None


@pytest.mark.parametrize(
    ("policy", "authorized"),
    [
        (
            SignerPolicy("signer@example.com", "https://issuer.example"),
            True,
        ),
        (
            SignerPolicy("different@example.com", "https://issuer.example"),
            False,
        ),
        (
            SignerPolicy("signer@example.com", "https://different.example"),
            False,
        ),
        (
            SignerPolicy("signér@example.com", "https://issuer.example"),
            False,
        ),
    ],
    ids=["match", "identity-mismatch", "issuer-mismatch", "unicode-mismatch"],
)
def test_signer_policy_authorizes_authenticated_identity(
    policy: SignerPolicy,
    authorized: bool,
) -> None:
    assert policy.authorize(_SIGNER) is authorized
    if authorized:
        policy.require_authorized(_SIGNER)
    else:
        with pytest.raises(AttestationError, match="does not match"):
            policy.require_authorized(_SIGNER)


def test_sigstore_settings_translate_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from conda_sigstore.settings import SigstoreSettings

    def invalid_settings(cls) -> SigstoreSettings:
        raise ValueError("trust_config does not exist")

    monkeypatch.setattr(SigstoreSettings, "current", classmethod(invalid_settings))

    with pytest.raises(AttestationError, match="Invalid conda-sigstore settings"):
        attestations_mod._sigstore_settings()


def test_verify_attestation_returns_upstream_statement_evidence(
    monkeypatch: pytest.MonkeyPatch,
    workspace_statement: WorkspaceAttestation,
) -> None:
    statement = workspace_statement.to_statement()
    evidence = VerifiedStatement(
        statement=statement,
        payload=statement.payload(),
        signer=_SIGNER,
        timestamps=("2026-08-22T12:00:00Z",),
    )

    class FakeVerifier:
        def verify_statement(self, bundle_json: str) -> VerifiedStatement:
            assert bundle_json == '{"bundle":true}'
            return evidence

    monkeypatch.setattr(
        attestations_mod,
        "_sigstore_settings",
        lambda: SimpleNamespace(trust_config=None, max_sidecar_bytes=1024),
    )
    from conda_sigstore.verification import SigstoreVerifier

    monkeypatch.setattr(
        SigstoreVerifier,
        "shared",
        classmethod(lambda cls, **kwargs: FakeVerifier()),
    )

    result = verify_attestation_bundle(b'{"bundle":true}')

    assert result is evidence
    assert result.statement is statement
    assert isinstance(result.statement, InTotoStatement)
    assert result.signer is _SIGNER


def test_sign_attestation_delegates_exact_validated_payload(
    monkeypatch: pytest.MonkeyPatch,
    workspace_statement: WorkspaceAttestation,
) -> None:
    payload = workspace_statement.payload()
    calls: list[bytes] = []

    def sign(statement, *, trust_config_path=None) -> str:
        calls.append(statement.payload())
        assert trust_config_path is None
        return '{"bundle":true}'

    monkeypatch.setattr(
        "conda_sigstore.attestation.sign_in_toto_statement",
        sign,
    )
    monkeypatch.setattr(
        attestations_mod,
        "_sigstore_settings",
        lambda: SimpleNamespace(trust_config=None, max_sidecar_bytes=1024),
    )
    with attestations_mod.conda_context._override("offline", False):
        result = sign_attestation_payload(payload)

    assert result == '{"bundle":true}'
    assert calls == [payload]


def test_sign_workspace_snapshot_delegates_exact_statement(
    monkeypatch: pytest.MonkeyPatch,
    workspace_snapshot: WorkspaceSnapshot,
) -> None:
    payloads: list[bytes] = []

    monkeypatch.setattr(
        attestations_mod,
        "sign_attestation_payload",
        lambda payload: payloads.append(payload) or '{"bundle":true}',
    )

    result = sign_workspace_snapshot(workspace_snapshot)

    assert result == '{"bundle":true}'
    assert len(payloads) == 1
    assert WorkspaceAttestation.from_payload(payloads[0]) == (
        WorkspaceAttestation.from_snapshot(workspace_snapshot)
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({}, None, id="match"),
        pytest.param(
            {"manifest_bytes": b"changed"},
            "manifest bytes",
            id="manifest-bytes",
        ),
        pytest.param(
            {"lockfile_bytes": b"changed"},
            "lockfile bytes",
            id="lockfile-bytes",
        ),
        pytest.param(
            {"manifest_name": "pixi.toml"},
            "paths or manifest format",
            id="manifest-path",
        ),
        pytest.param(
            {"manifest_format": "pixi-toml"},
            "paths or manifest format",
            id="manifest-format",
        ),
    ],
)
def test_verify_workspace_snapshot_binds_authenticated_payload(
    monkeypatch: pytest.MonkeyPatch,
    workspace_snapshot: WorkspaceSnapshot,
    changes: dict[str, object],
    message: str | None,
) -> None:
    signer_policy = SignerPolicy("signer@example.com", "https://issuer.example")
    attestation = WorkspaceAttestation.from_snapshot(workspace_snapshot)
    statement = attestation.to_statement()
    evidence = VerifiedStatement(
        statement=statement,
        payload=statement.payload(),
        signer=_SIGNER,
        timestamps=("2026-08-22T12:00:00Z",),
    )
    calls: list[bytes] = []

    def verify(bundle_bytes: bytes) -> VerifiedStatement:
        calls.append(bundle_bytes)
        return evidence

    monkeypatch.setattr(attestations_mod, "verify_attestation_bundle", verify)
    candidate = replace(workspace_snapshot, **changes)

    if message is None:
        result = verify_workspace_snapshot(b"bundle", candidate, signer_policy)
        assert result.attestation == attestation
        assert result.evidence is evidence
        assert result.authorized is True
        assert result.signer is _SIGNER
        assert result.predicate_type == WORKSPACE_ATTESTATION_PREDICATE_TYPE
        result.require_authorized()
    else:
        with pytest.raises(AttestationError, match=message):
            verify_workspace_snapshot(b"bundle", candidate, signer_policy)

    assert calls == [b"bundle"]


@pytest.mark.parametrize("kind", ["direct", "hardlink"], ids=["direct", "hardlink"])
def test_attestation_output_rejects_protected_input_alias(
    tmp_path: Path,
    kind: str,
) -> None:
    protected = tmp_path / "conda.lock"
    protected.write_text("lock", encoding="utf-8")
    output = protected
    if kind == "hardlink":
        output = tmp_path / "bundle.json"
        output.hardlink_to(protected)

    with pytest.raises(AttestationError, match="protected input"):
        AttestationOutput.prepare(
            output,
            protected_paths=(protected,),
            maximum_bytes=1024,
        )


def test_attestation_output_rejects_replacement_during_signing(tmp_path: Path) -> None:
    output = tmp_path / "bundle.json"
    output.write_text("old", encoding="utf-8")
    prepared = AttestationOutput.prepare(output, maximum_bytes=1024)
    output.write_text("concurrent", encoding="utf-8")

    with pytest.raises(AttestationError, match="changed before publication"):
        prepared.write('{"bundle":true}')

    assert output.read_text(encoding="utf-8") == "concurrent"


def test_attestation_output_preserves_publication_recovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "bundle.json"
    recovery = tmp_path / ".bundle.json.recovery.rollback"
    prepared = AttestationOutput.prepare(output, maximum_bytes=1024)

    def fail_publication(*args, **kwargs):
        raise ValueError(f"Output changed. Recovery entry: {recovery}")

    monkeypatch.setattr(AttestationOutput, "_publish_content", fail_publication)

    with pytest.raises(AttestationError, match=str(recovery)):
        prepared.write('{"bundle":true}')


def test_attestation_output_rejects_parent_replacement_during_signing(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "sidecars"
    parent.mkdir()
    output = parent / "bundle.json"
    prepared = AttestationOutput.prepare(output, maximum_bytes=1024)
    original_parent = tmp_path / "original-sidecars"
    parent.rename(original_parent)
    parent.mkdir()
    marker = parent / "replacement.txt"
    marker.write_text("replacement directory", encoding="utf-8")

    with pytest.raises(AttestationError, match="changed before publication"):
        prepared.write('{"bundle":true}')

    assert list(parent.iterdir()) == [marker]
    assert not (original_parent / output.name).exists()


def test_attestation_output_path_fallback_does_not_write_to_replaced_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        paths_mod,
        "_SUPPORTS_ANCHORED_DIRECTORY_OPERATIONS",
        False,
    )
    parent = tmp_path / "sidecars"
    parent.mkdir()
    output = parent / "bundle.json"
    prepared = AttestationOutput.prepare(output, maximum_bytes=1024)
    displaced = tmp_path / "original-sidecars"
    original_require = attestations_mod.require_directory_identity
    replaced = False

    def replace_parent_after_check(*args: object, **kwargs: object) -> None:
        nonlocal replaced
        original_require(*args, **kwargs)
        if not replaced:
            parent.rename(displaced)
            parent.mkdir()
            replaced = True

    monkeypatch.setattr(
        attestations_mod,
        "require_directory_identity",
        replace_parent_after_check,
    )

    with pytest.raises(AttestationError, match="changed before publication"):
        prepared.write('{"bundle":true}')

    assert replaced is True
    assert not output.exists()
    assert not (displaced / output.name).exists()


def test_attestation_output_rejects_oversized_bundle(tmp_path: Path) -> None:
    output = tmp_path / "bundle.json"
    prepared = AttestationOutput.prepare(output, maximum_bytes=4)

    with pytest.raises(AttestationError, match="exceeds the 4-byte limit"):
        prepared.write("1234")

    assert not output.exists()


@pytest.mark.parametrize(
    "previous_content",
    [b"previous bundle\n", None],
    ids=["existing-output", "missing-output"],
)
@pytest.mark.parametrize(
    ("failure", "expected_exception", "message"),
    [
        ("later-operation", RuntimeError, "later failure"),
        ("writer-after-publication", AttestationError, "changed before publication"),
    ],
    ids=["later-operation", "writer-after-publication"],
)
def test_attestation_output_reversible_write_restores_previous_output_on_failure(
    tmp_path: Path,
    previous_content: bytes | None,
    failure: str,
    expected_exception: type[BaseException],
    message: str,
    fail_attestation_writer_after_publication: Callable[[], None],
) -> None:
    output = tmp_path / "bundle.json"
    if previous_content is not None:
        output.write_bytes(previous_content)
    prepared = AttestationOutput.prepare(output, maximum_bytes=1024)
    if failure == "writer-after-publication":
        fail_attestation_writer_after_publication()

    with pytest.raises(expected_exception, match=message):
        with prepared.reversible_write('{"bundle":true}'):
            if failure == "later-operation":
                raise RuntimeError("later failure")
            pytest.fail("The writer failure must prevent the context body")

    if previous_content is None:
        assert not output.exists()
    else:
        assert output.read_bytes() == previous_content


def test_attestation_output_reversible_write_preserves_concurrent_replacement(
    tmp_path: Path,
) -> None:
    output = tmp_path / "bundle.json"
    output.write_bytes(b"previous bundle\n")
    prepared = AttestationOutput.prepare(output, maximum_bytes=1024)

    with pytest.raises(RuntimeError, match="later failure"):
        with prepared.reversible_write('{"bundle":true}'):
            output.write_bytes(b"concurrent bundle\n")
            raise RuntimeError("later failure")

    assert output.read_bytes() == b"concurrent bundle\n"


@pytest.mark.parametrize(
    "unsafe_input",
    ["oversized", "symlink", "invalid-utf8"],
    ids=["oversized", "symlink", "invalid-utf8"],
)
def test_attestation_bundle_read_rejects_unsafe_input(
    tmp_path: Path,
    unsafe_input: str,
) -> None:
    bundle = tmp_path / "bundle.json"
    if unsafe_input == "oversized":
        bundle.write_bytes(b"x" * 5)
    elif unsafe_input == "symlink":
        target = tmp_path / "target.json"
        target.write_bytes(b"{}")
        bundle.symlink_to(target)
    else:
        bundle.write_bytes(b"\xff")

    with pytest.raises(AttestationError, match="read Sigstore bundle safely"):
        read_attestation_bundle(bundle, max_bytes=4)
