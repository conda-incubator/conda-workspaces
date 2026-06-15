"""Sigstore attestations for workspace manifests and lockfiles."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .archive import file_sha256, parse_relative_archive_path
from .exceptions import AttestationError
from .lockfile import FORMAT as LOCKFILE_FORMAT
from .receipts import IN_TOTO_STATEMENT_TYPE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

IN_TOTO_PAYLOAD_TYPE = "application/vnd.in-toto+json"
WORKSPACE_ATTESTATION_PREDICATE_TYPE = (
    "https://conda-incubator.github.io/conda-workspaces/"
    "workspace-attestation-1.schema.json"
)
WORKSPACE_ATTESTATION_FORMAT_VERSION = 1
SIGSTORE_JSON_SUFFIX = ".sigstore.json"

SignerCallback = Callable[[bytes, str | None], str]
BundleVerifierCallback = Callable[[str, tuple["TrustIdentity", ...]], tuple[str, bytes]]


@dataclass(frozen=True)
class TrustIdentity:
    """Sigstore certificate identity expected during verification."""

    identity: str
    issuer: str

    def __post_init__(self) -> None:
        if not self.identity:
            raise AttestationError("Trusted Sigstore identity cannot be empty.")
        if not self.issuer:
            raise AttestationError("Trusted Sigstore issuer cannot be empty.")


@dataclass(frozen=True)
class WorkspaceAttestation:
    """Unsigned in-toto Statement payload signed by a Sigstore bundle."""

    statement: dict[str, Any]

    @classmethod
    def build(
        cls,
        *,
        root: Path,
        manifest_path: Path,
        lockfile_path: Path,
    ) -> WorkspaceAttestation:
        """Build a workspace attestation statement for manifest and lockfile paths."""
        root = root.resolve()
        manifest_path = manifest_path.resolve()
        lockfile_path = lockfile_path.resolve()

        if not manifest_path.is_file():
            raise AttestationError("Cannot sign workspace: manifest was not found.")
        if not lockfile_path.is_file():
            raise AttestationError(
                "Cannot sign workspace: no conda.lock found.",
                hints=["Run 'conda workspace lock' first."],
            )

        manifest_name = workspace_relative_name(
            root,
            manifest_path,
            "workspace manifest",
        )
        lockfile_name = workspace_relative_name(
            root,
            lockfile_path,
            "workspace lockfile",
        )
        attestation = cls(
            {
                "_type": IN_TOTO_STATEMENT_TYPE,
                "subject": [
                    file_subject(manifest_name, manifest_path),
                    file_subject(lockfile_name, lockfile_path),
                ],
                "predicateType": WORKSPACE_ATTESTATION_PREDICATE_TYPE,
                "predicate": {
                    "version": WORKSPACE_ATTESTATION_FORMAT_VERSION,
                    "workspace": {
                        "manifest": manifest_name,
                        "lockfile": lockfile_name,
                    },
                    "manifest": {"format": manifest_format(manifest_path)},
                    "lockfile": {"format": LOCKFILE_FORMAT},
                },
            }
        )
        attestation.validate()
        return attestation

    @classmethod
    def load_payload(cls, payload: bytes) -> WorkspaceAttestation:
        """Load an in-toto Statement payload, rejecting duplicate JSON keys."""

        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise AttestationError(
                        f"Invalid attestation: duplicate JSON key '{key}'."
                    )
                result[key] = value
            return result

        try:
            data = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
        except UnicodeDecodeError as exc:
            raise AttestationError(
                "Invalid attestation payload: expected UTF-8."
            ) from exc
        except json.JSONDecodeError as exc:
            raise AttestationError(
                "Invalid attestation payload: expected JSON."
            ) from exc

        if not isinstance(data, dict):
            raise AttestationError("Invalid attestation: expected a JSON object.")

        attestation = cls(cast("dict[str, Any]", data))
        attestation.validate()
        return attestation

    @property
    def predicate(self) -> Mapping[str, object]:
        """Return the Statement predicate object."""
        value = self.statement.get("predicate")
        if not isinstance(value, dict):
            raise AttestationError("Invalid attestation: predicate must be an object.")
        return cast("Mapping[str, object]", value)

    @property
    def format_version(self) -> int:
        """Return the supported predicate format version."""
        value = self.predicate.get("version")
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value != WORKSPACE_ATTESTATION_FORMAT_VERSION
        ):
            raise AttestationError("Invalid attestation: unsupported format version.")
        return value

    @property
    def workspace_paths(self) -> tuple[str, str]:
        """Return validated workspace-relative manifest and lockfile paths."""
        workspace = self.predicate.get("workspace")
        if not isinstance(workspace, dict):
            raise AttestationError(
                "Invalid attestation: predicate.workspace must be an object."
            )
        workspace_data = cast("Mapping[str, object]", workspace)
        manifest = workspace_data.get("manifest")
        lockfile = workspace_data.get("lockfile")
        if not isinstance(manifest, str) or not isinstance(lockfile, str):
            raise AttestationError("Invalid attestation: workspace paths are missing.")
        return (
            relative_workspace_path(manifest, "workspace manifest").as_posix(),
            relative_workspace_path(lockfile, "workspace lockfile").as_posix(),
        )

    @property
    def subject_digests(self) -> dict[str, str]:
        """Return attestation subjects keyed by name."""
        subjects = self.statement.get("subject")
        if not isinstance(subjects, list) or not subjects:
            raise AttestationError(
                "Invalid attestation: subject must be a non-empty list."
            )

        result: dict[str, str] = {}
        for subject in subjects:
            if not isinstance(subject, dict):
                raise AttestationError(
                    "Invalid attestation: subject entries must be objects."
                )
            subject_data = cast("Mapping[str, object]", subject)
            name = subject_data.get("name")
            digest = subject_data.get("digest")
            if not isinstance(name, str) or not name:
                raise AttestationError(
                    "Invalid attestation: subject entry is missing a name."
                )
            if name in result:
                raise AttestationError("Invalid attestation: duplicate subject name.")
            if not isinstance(digest, dict):
                raise AttestationError(
                    "Invalid attestation: subject entry is missing a sha256 digest."
                )
            digest_data = cast("Mapping[str, object]", digest)
            sha256 = digest_data.get("sha256")
            if not isinstance(sha256, str):
                raise AttestationError(
                    "Invalid attestation: subject entry is missing a sha256 digest."
                )
            result[name] = sha256_digest(sha256)
        return result

    def validate(self) -> None:
        """Validate only fields used by workspace attestation verification."""
        if self.statement.get("_type") != IN_TOTO_STATEMENT_TYPE:
            raise AttestationError(
                "Invalid attestation: unsupported in-toto statement type."
            )
        if self.statement.get("predicateType") != WORKSPACE_ATTESTATION_PREDICATE_TYPE:
            raise AttestationError("Invalid attestation: unsupported predicate type.")
        self.subject_digests
        self.workspace_paths
        self.format_version

    def payload(self) -> bytes:
        """Return the statement as stable JSON bytes suitable for DSSE signing."""
        self.validate()
        return (json.dumps(self.statement, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )

    def verify_files(
        self,
        *,
        root: Path,
        manifest_path: Path | None = None,
        lockfile_path: Path | None = None,
    ) -> None:
        """Verify manifest and lockfile paths below *root* against subject digests."""
        root = root.resolve()
        manifest_name, lockfile_name = self.workspace_paths
        if manifest_path is not None:
            actual_manifest = workspace_relative_name(
                root,
                manifest_path.resolve(),
                "workspace manifest",
            )
            if actual_manifest != manifest_name:
                raise AttestationError(
                    "Attestation manifest path does not match this workspace."
                )
        if lockfile_path is not None:
            actual_lockfile = workspace_relative_name(
                root,
                lockfile_path.resolve(),
                "workspace lockfile",
            )
            if actual_lockfile != lockfile_name:
                raise AttestationError(
                    "Attestation lockfile path does not match this workspace."
                )
        expected_paths = {
            manifest_name: (
                manifest_path.resolve()
                if manifest_path is not None
                else workspace_file(root, manifest_name, "workspace manifest")
            ),
            lockfile_name: (
                lockfile_path.resolve()
                if lockfile_path is not None
                else workspace_file(root, lockfile_name, "workspace lockfile")
            ),
        }

        digests = self.subject_digests
        for name, path in expected_paths.items():
            relative_workspace_path(name, "workspace file")
            workspace_file(root, name, "workspace file")
            if not path.is_file():
                raise AttestationError(f"Attested file not found: {name}")
            expected = digests.get(name)
            if expected is None:
                raise AttestationError(f"Attestation is missing subject '{name}'.")
            actual = file_sha256(path)
            if actual != expected:
                raise AttestationError(
                    f"Attested file digest mismatch for '{name}'.",
                    hints=[
                        f"Expected sha256 {expected}, got {actual}.",
                        "Regenerate the lockfile and attestation from trusted inputs.",
                    ],
                )


def default_attestation_path(lockfile_path: Path) -> Path:
    """Return the default Sigstore bundle sidecar path for *lockfile_path*."""
    return lockfile_path.with_name(f"{lockfile_path.name}{SIGSTORE_JSON_SUFFIX}")


def file_subject(name: str, path: Path) -> dict[str, object]:
    """Return an in-toto subject for a file."""
    return {"name": name, "digest": {"sha256": file_sha256(path)}}


def workspace_relative_name(root: Path, path: Path, label: str) -> str:
    """Return *path* as a safe POSIX path relative to *root*."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise AttestationError(
            f"Cannot attest {label}: path is outside the workspace root."
        ) from None
    return relative_workspace_path(relative.as_posix(), label).as_posix()


def relative_workspace_path(path: str, label: str) -> Path:
    """Return *path* as a validated workspace-relative path."""
    try:
        return Path(parse_relative_archive_path(path))
    except ValueError as exc:
        raise AttestationError(f"Invalid attested {label} path: {path!r}.") from exc


def workspace_file(root: Path, relative: str, label: str) -> Path:
    """Return a resolved workspace file path and reject symlink escapes."""
    path = root.joinpath(*relative_workspace_path(relative, label).parts).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise AttestationError(
            f"Invalid attested {label} path: {relative!r} escapes the workspace."
        ) from None
    return path


def sha256_digest(value: str) -> str:
    """Validate and normalize a SHA-256 hex digest."""
    if len(value) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise AttestationError("Invalid attestation: sha256 digest is malformed.")
    return value.lower()


def manifest_format(path: Path) -> str:
    """Return the manifest format label for *path*."""
    return {
        "conda.toml": "conda-toml",
        "pixi.toml": "pixi-toml",
        "pyproject.toml": "pyproject-toml",
    }.get(path.name, path.suffix.lstrip(".") or path.name)


def trust_identities_from_cli(
    identity: str | None,
    issuer: str | None,
) -> tuple[TrustIdentity, ...]:
    """Return a trust policy from ``--cert-identity`` / ``--cert-oidc-issuer``."""
    if identity is None and issuer is None:
        return ()
    if identity is None or issuer is None:
        raise AttestationError(
            "--cert-identity and --cert-oidc-issuer must be passed together."
        )
    return (TrustIdentity(identity=identity, issuer=issuer),)


def write_workspace_attestation(
    *,
    root: Path,
    manifest_path: Path,
    lockfile_path: Path,
    bundle_path: Path | None = None,
    identity_token: str | None = None,
    signer: SignerCallback | None = None,
) -> Path:
    """Build, sign, and write a Sigstore bundle for a workspace attestation."""
    attestation = WorkspaceAttestation.build(
        root=root,
        manifest_path=manifest_path,
        lockfile_path=lockfile_path,
    )
    payload = attestation.payload()
    bundle_json = (
        signer(payload, identity_token)
        if signer is not None
        else sign_payload_with_sigstore(payload, identity_token)
    )
    target = bundle_path or default_attestation_path(lockfile_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(bundle_json.rstrip() + "\n", encoding="utf-8")
    return target


def verify_workspace_attestation(
    *,
    root: Path,
    identities: tuple[TrustIdentity, ...],
    manifest_path: Path | None = None,
    lockfile_path: Path | None = None,
    bundle_path: Path | None = None,
    bundle_verifier: BundleVerifierCallback | None = None,
) -> WorkspaceAttestation:
    """Verify a Sigstore bundle and the workspace files named by its payload."""
    if not identities:
        raise AttestationError(
            "No trusted Sigstore identity configured for attestation verification.",
            hints=[
                "Pass --cert-identity and --cert-oidc-issuer with --verify.",
            ],
        )

    root = root.resolve()
    lock = lockfile_path or root / "conda.lock"
    target = bundle_path or default_attestation_path(lock)
    try:
        bundle_json = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise AttestationError(f"Attestation bundle not found: {target}") from exc

    payload_type, payload = (
        bundle_verifier(bundle_json, identities)
        if bundle_verifier is not None
        else verify_sigstore_bundle(bundle_json, identities)
    )
    if payload_type != IN_TOTO_PAYLOAD_TYPE:
        raise AttestationError(
            f"Invalid attestation payload type: {payload_type!r}.",
            hints=[f"Expected {IN_TOTO_PAYLOAD_TYPE!r}."],
        )

    attestation = WorkspaceAttestation.load_payload(payload)
    attestation.verify_files(
        root=root,
        manifest_path=manifest_path,
        lockfile_path=lockfile_path,
    )
    return attestation


def sign_payload_with_sigstore(payload: bytes, identity_token: str | None) -> str:
    """Sign an in-toto Statement payload with sigstore-python."""
    try:
        dsse = importlib.import_module("sigstore.dsse")
        models = importlib.import_module("sigstore.models")
        oidc = importlib.import_module("sigstore.oidc")
        sign = importlib.import_module("sigstore.sign")
    except ImportError as exc:
        raise AttestationError(
            "Sigstore signing support is not installed.",
            hints=["Install conda-workspaces with the 'signing' extra."],
        ) from exc

    try:
        trust_config = models.ClientTrustConfig.production()
        context = sign.SigningContext.from_trust_config(trust_config)
        raw_token = identity_token or oidc.detect_credential()
        token = (
            oidc.IdentityToken(raw_token)
            if raw_token is not None
            else oidc.Issuer(
                trust_config.signing_config.get_oidc_url()
            ).identity_token()
        )
        with context.signer(token, cache=True) as signer:
            bundle = signer.sign_dsse(dsse.Statement(payload))
        return bundle.to_json()
    except Exception as exc:
        raise AttestationError(f"Failed to sign workspace attestation: {exc}") from exc


def verify_sigstore_bundle(
    bundle_json: str,
    identities: tuple[TrustIdentity, ...],
) -> tuple[str, bytes]:
    """Verify a Sigstore DSSE bundle and return its payload type and bytes."""
    try:
        models = importlib.import_module("sigstore.models")
        policy_module = importlib.import_module("sigstore.verify.policy")
        verifier_module = importlib.import_module("sigstore.verify.verifier")
    except ImportError as exc:
        raise AttestationError(
            "Sigstore verification support is not installed.",
            hints=["Install conda-workspaces with the 'signing' extra."],
        ) from exc

    try:
        bundle = models.Bundle.from_json(bundle_json)
        policies = [
            policy_module.Identity(identity=item.identity, issuer=item.issuer)
            for item in identities
        ]
        policy = policies[0] if len(policies) == 1 else policy_module.AnyOf(policies)
        return verifier_module.Verifier.production().verify_dsse(bundle, policy)
    except Exception as exc:
        raise AttestationError(
            f"Failed to verify workspace attestation: {exc}"
        ) from exc
