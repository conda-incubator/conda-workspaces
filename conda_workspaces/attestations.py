"""Workspace-specific statements backed by conda-sigstore."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from conda.base.context import context as conda_context

from .exceptions import AttestationError, FileRecoveryError
from .lockfile import FORMAT as LOCKFILE_FORMAT
from .manifests.base import ManifestParser
from .paths import (
    anchored_directory,
    atomic_binary_writer,
    atomic_binary_writer_at,
    directory_identity,
    output_paths_collide,
    parse_relative_posix_path,
    read_regular_file_bytes,
    read_regular_file_bytes_with_generation,
    remove_file_generation,
    require_directory_identity,
    validate_path_parent,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from conda_sigstore.evidence import SignerIdentity
    from conda_sigstore.settings import SigstoreSettings
    from conda_sigstore.statements import InTotoStatement
    from conda_sigstore.verification import VerifiedStatement

    from .paths import DirectoryIdentity, FileGeneration
    from .publication import WorkspaceSnapshot

WORKSPACE_ATTESTATION_PREDICATE_TYPE = (
    "https://conda-incubator.github.io/conda-workspaces/"
    "workspace-attestation-1.schema.json"
)
WORKSPACE_ATTESTATION_FORMAT_VERSION = 1
SIGSTORE_JSON_SUFFIX = ".sigstore.json"
MAX_ATTESTATION_BYTES = 10 * 1024 * 1024

_DEPENDENCY_MESSAGE = (
    "Workspace attestations require conda-sigstore >=0.1.0. Install or upgrade "
    "it in the environment where conda is installed."
)


@dataclass(frozen=True, slots=True)
class SignerPolicy:
    """A receiver-owned Sigstore certificate identity requirement."""

    identity: str
    issuer: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity.strip():
            raise AttestationError("Sigstore certificate identity cannot be empty.")
        if not isinstance(self.issuer, str) or not self.issuer.strip():
            raise AttestationError("Sigstore OIDC issuer cannot be empty.")

    @classmethod
    def from_values(
        cls,
        identity: str | None,
        issuer: str | None,
    ) -> SignerPolicy | None:
        """Build an authorization policy from a required pair of CLI values."""
        if identity is None and issuer is None:
            return None
        if identity is None or issuer is None:
            raise AttestationError(
                "--cert-identity and --cert-oidc-issuer must be supplied together."
            )
        return cls(identity, issuer)

    def authorize(self, signer: SignerIdentity) -> bool:
        """Return whether authenticated *signer* matches this exact policy."""
        return self.identity == signer.identity and self.issuer == signer.issuer

    def require_authorized(self, signer: SignerIdentity) -> None:
        """Reject an authenticated signer that does not match this policy."""
        if not self.authorize(signer):
            raise AttestationError(
                "The authenticated Sigstore signer does not match the expected "
                "certificate identity and issuer."
            )


@dataclass(slots=True)
class AttestationOutput:
    """A generation-bound destination for one Sigstore bundle."""

    path: Path
    generation: FileGeneration | None
    previous_content: bytes | None = field(repr=False, default=None)
    maximum_bytes: int = MAX_ATTESTATION_BYTES
    directory_descriptor: int | None = None
    parent_path: Path = field(repr=False, default=Path("."))
    parent_identity: DirectoryIdentity | None = field(
        repr=False,
        default=None,
    )
    published_content: bytes | None = field(init=False, repr=False, default=None)
    published_generation: FileGeneration | None = field(
        init=False,
        repr=False,
        default=None,
    )

    @staticmethod
    def _capture_output(
        path: Path,
        *,
        maximum_bytes: int,
        directory_descriptor: int | None,
    ) -> tuple[bytes | None, FileGeneration | None]:
        """Capture an existing bounded output or report an absent leaf."""
        try:
            return read_regular_file_bytes_with_generation(
                path,
                maximum_bytes=maximum_bytes,
                label="Sigstore bundle output",
                directory_descriptor=directory_descriptor,
            )
        except ValueError:
            try:
                if directory_descriptor is None:
                    path.lstat()
                else:
                    os.stat(
                        path.name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
            except FileNotFoundError:
                return None, None
            raise

    @classmethod
    def prepare(
        cls,
        path: Path,
        *,
        protected_paths: Iterable[Path] = (),
        maximum_bytes: int | None = None,
        directory_descriptor: int | None = None,
        create_parent: bool = False,
    ) -> AttestationOutput:
        """Validate the output and anchor its parent before signing starts."""
        resolved_maximum = (
            _sigstore_settings().max_sidecar_bytes
            if maximum_bytes is None
            else maximum_bytes
        )
        if isinstance(resolved_maximum, bool) or resolved_maximum < 1:
            raise AttestationError("Attestation size limit must be positive.")
        parent_path = path.parent.absolute()
        try:
            for protected_path in protected_paths:
                if output_paths_collide(path, protected_path):
                    raise AttestationError(
                        f"Attestation output cannot replace protected input: {path}"
                    )
            if directory_descriptor is not None:
                parent_stat = os.fstat(directory_descriptor)
                if not stat.S_ISDIR(parent_stat.st_mode):
                    raise NotADirectoryError(
                        f"Attestation output parent is not a directory: {parent_path}"
                    )
                live_parent = parent_path.lstat()
                if not stat.S_ISDIR(live_parent.st_mode) or (
                    live_parent.st_dev,
                    live_parent.st_ino,
                ) != (parent_stat.st_dev, parent_stat.st_ino):
                    raise NotADirectoryError(
                        "Attestation output parent changed while opening: "
                        f"{parent_path}"
                    )
                previous_content, generation = cls._capture_output(
                    path,
                    maximum_bytes=resolved_maximum,
                    directory_descriptor=directory_descriptor,
                )
            elif not parent_path.exists() and not parent_path.is_symlink():
                if create_parent:
                    with anchored_directory(parent_path, create=True):
                        pass
                else:
                    validate_path_parent(path)
                    generation = None
                    return cls(
                        path=path,
                        generation=generation,
                        previous_content=None,
                        maximum_bytes=resolved_maximum,
                        parent_path=parent_path,
                    )
                with anchored_directory(parent_path) as parent_descriptor:
                    assert parent_descriptor is not None or parent_path.is_dir()
                    parent_stat = (
                        os.fstat(parent_descriptor)
                        if parent_descriptor is not None
                        else parent_path.lstat()
                    )
                    previous_content, generation = cls._capture_output(
                        parent_path / path.name,
                        maximum_bytes=resolved_maximum,
                        directory_descriptor=parent_descriptor,
                    )
            else:
                with anchored_directory(parent_path) as parent_descriptor:
                    parent_stat = (
                        os.fstat(parent_descriptor)
                        if parent_descriptor is not None
                        else parent_path.lstat()
                    )
                    previous_content, generation = cls._capture_output(
                        parent_path / path.name,
                        maximum_bytes=resolved_maximum,
                        directory_descriptor=parent_descriptor,
                    )
            parent_identity = directory_identity(parent_stat)
        except AttestationError:
            raise
        except (OSError, ValueError) as exc:
            raise AttestationError(
                f"Attestation output cannot be used safely: {path}"
            ) from exc
        return cls(
            path=path,
            generation=generation,
            previous_content=previous_content,
            maximum_bytes=resolved_maximum,
            directory_descriptor=directory_descriptor,
            parent_path=parent_path,
            parent_identity=parent_identity,
        )

    def write(self, bundle_json: str) -> Path:
        """Publish through the parent directory anchored before signing."""
        content = bundle_json.rstrip("\n") + "\n"
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.maximum_bytes:
            raise AttestationError(
                f"Sigstore bundle exceeds the {self.maximum_bytes}-byte limit."
            )
        if self.parent_identity is None:
            raise AttestationError(
                "Attestation output parent was not prepared for publication: "
                f"{self.path}"
            )
        self.published_content = None
        self.published_generation = None
        try:
            published, published_generation = self._publish_content(
                content_bytes,
                expected_generation=self.generation,
                expected_sha256=(
                    hashlib.sha256(self.previous_content).hexdigest()
                    if self.previous_content is not None
                    else None
                ),
            )
            if (
                published != content_bytes
                or self.published_content != content_bytes
                or self.published_generation != published_generation
            ):
                raise ValueError(
                    f"Attestation output changed during publication: {self.path}"
                )
        except (OSError, ValueError) as exc:
            raise AttestationError(
                f"Attestation output changed before publication: {self.path}. {exc}"
            ) from exc
        return self.path

    @contextmanager
    def reversible_write(self, bundle_json: str) -> Iterator[Path]:
        """Restore the prepared output when a later operation fails."""
        try:
            written = self.write(bundle_json)
            yield written
        except BaseException as operation_error:
            if (
                self.published_content is not None
                and self.published_generation is not None
            ):
                try:
                    self.restore()
                except FileRecoveryError as recovery_error:
                    if isinstance(operation_error, FileRecoveryError):
                        raise operation_error.combine(
                            recovery_error,
                            reason=(
                                "Attestation publication and rollback retained"
                                " recovery entries."
                            ),
                        ) from recovery_error
                    raise
                except BaseException as recovery_error:
                    if isinstance(operation_error, FileRecoveryError):
                        raise operation_error from recovery_error
                    raise
            raise

    def restore(self) -> bool:
        """Restore the prepared output unless its publication was replaced."""
        if self.published_content is None or self.published_generation is None:
            raise RuntimeError("Attestation output publication was not captured")
        try:
            current_content, current_generation = self._read_content()
        except (OSError, ValueError):
            return False
        if (
            current_content != self.published_content
            or current_generation != self.published_generation
        ):
            return False

        if self.previous_content is None:
            try:
                removed = remove_file_generation(
                    self.path,
                    self.published_generation,
                    expected_content=self.published_content,
                    directory_descriptor=self.directory_descriptor,
                    expected_parent_identity=self.parent_identity,
                )
            except (OSError, ValueError) as exc:
                raise AttestationError(
                    f"Attestation output cannot be restored safely: {self.path}. {exc}"
                ) from exc
            if removed:
                self.generation = None
                self.published_content = None
                self.published_generation = None
            return removed

        try:
            restored_content, restored_generation = self._publish_content(
                self.previous_content,
                expected_generation=self.published_generation,
                expected_sha256=hashlib.sha256(self.published_content).hexdigest(),
            )
            if restored_content != self.previous_content:
                return False
        except (OSError, ValueError) as exc:
            raise AttestationError(
                f"Attestation output cannot be restored safely: {self.path}. {exc}"
            ) from exc
        self.generation = restored_generation
        self.published_content = None
        self.published_generation = None
        return True

    def _publish_content(
        self,
        content: bytes,
        *,
        expected_generation: FileGeneration | None,
        expected_sha256: str | None,
    ) -> tuple[bytes, FileGeneration]:
        """Publish and recapture exact output bytes through the prepared parent."""
        if self.parent_identity is None:
            raise ValueError("Attestation output parent was not captured")

        def capture_publication(generation: FileGeneration) -> None:
            self.published_content = content
            self.published_generation = generation

        if self.directory_descriptor is not None:
            require_directory_identity(
                self.parent_path,
                self.parent_identity,
                directory_descriptor=self.directory_descriptor,
            )
            with atomic_binary_writer_at(
                self.directory_descriptor,
                self.path.name,
                display_path=self.path,
                expected_generation=expected_generation,
                expected_sha256=expected_sha256,
                capture_generation=capture_publication,
            ) as stream:
                stream.write(content)
            require_directory_identity(
                self.parent_path,
                self.parent_identity,
                directory_descriptor=self.directory_descriptor,
            )
            return read_regular_file_bytes_with_generation(
                self.path,
                maximum_bytes=self.maximum_bytes,
                label="Sigstore bundle output",
                directory_descriptor=self.directory_descriptor,
            )

        with anchored_directory(self.parent_path) as parent_descriptor:
            require_directory_identity(
                self.parent_path,
                self.parent_identity,
                directory_descriptor=parent_descriptor,
            )
            target = self.parent_path / self.path.name
            if parent_descriptor is None:
                with atomic_binary_writer(
                    target,
                    expected_generation=expected_generation,
                    expected_sha256=expected_sha256,
                    expected_parent_identity=self.parent_identity,
                    capture_generation=capture_publication,
                ) as stream:
                    stream.write(content)
            else:
                with atomic_binary_writer_at(
                    parent_descriptor,
                    self.path.name,
                    display_path=self.path,
                    expected_generation=expected_generation,
                    expected_sha256=expected_sha256,
                    capture_generation=capture_publication,
                ) as stream:
                    stream.write(content)
            require_directory_identity(
                self.parent_path,
                self.parent_identity,
                directory_descriptor=parent_descriptor,
            )
            return read_regular_file_bytes_with_generation(
                target,
                maximum_bytes=self.maximum_bytes,
                label="Sigstore bundle output",
                directory_descriptor=parent_descriptor,
            )

    def _read_content(self) -> tuple[bytes, FileGeneration]:
        """Read the live output through the parent captured by :meth:`prepare`."""
        if self.parent_identity is None:
            raise ValueError("Attestation output parent was not captured")
        if self.directory_descriptor is not None:
            require_directory_identity(
                self.parent_path,
                self.parent_identity,
                directory_descriptor=self.directory_descriptor,
            )
            return read_regular_file_bytes_with_generation(
                self.path,
                maximum_bytes=self.maximum_bytes,
                label="Sigstore bundle output",
                directory_descriptor=self.directory_descriptor,
            )
        with anchored_directory(self.parent_path) as parent_descriptor:
            require_directory_identity(
                self.parent_path,
                self.parent_identity,
                directory_descriptor=parent_descriptor,
            )
            return read_regular_file_bytes_with_generation(
                self.parent_path / self.path.name,
                maximum_bytes=self.maximum_bytes,
                label="Sigstore bundle output",
                directory_descriptor=parent_descriptor,
            )


@dataclass(frozen=True, slots=True)
class WorkspaceAttestation:
    """A strict manifest and canonical lockfile in-toto statement."""

    manifest_path: str
    manifest_sha256: str
    manifest_format: str
    lockfile_path: str
    lockfile_sha256: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.manifest_path, "manifest path"),
            (self.lockfile_path, "lockfile path"),
        ):
            try:
                parse_relative_posix_path(value, require_canonical=True)
            except ValueError as exc:
                raise AttestationError(f"Invalid workspace {label}: {value!r}") from exc
        if self.manifest_path == self.lockfile_path:
            raise AttestationError(
                "Workspace manifest and lockfile subjects must be distinct."
            )
        self._validate_digest(self.manifest_sha256, "manifest sha256")
        self._validate_digest(self.lockfile_sha256, "lockfile sha256")
        manifest_parser = ManifestParser.for_exporter_format(self.manifest_format)
        if manifest_parser is None:
            raise AttestationError(
                f"Unsupported workspace manifest format: {self.manifest_format!r}"
            )
        if self.manifest_path != manifest_parser.manifest_filename:
            raise AttestationError(
                "Workspace manifest path does not match its declared format."
            )
        if self.lockfile_path != "conda.lock":
            raise AttestationError(
                "Workspace attestation requires the canonical conda.lock path."
            )

    @staticmethod
    def _validate_digest(value: object, label: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or value != value.lower()
            or not all(character in "0123456789abcdef" for character in value)
        ):
            raise AttestationError(
                f"{label} must be a lowercase 64-character hexadecimal string."
            )
        return value

    @staticmethod
    def _digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def build(
        cls,
        *,
        manifest_path: str,
        manifest_bytes: bytes,
        manifest_format: str,
        lockfile_path: str,
        lockfile_bytes: bytes,
    ) -> WorkspaceAttestation:
        """Build a statement model from exact accepted workspace bytes."""
        return cls(
            manifest_path=manifest_path,
            manifest_sha256=cls._digest(manifest_bytes),
            manifest_format=manifest_format,
            lockfile_path=lockfile_path,
            lockfile_sha256=cls._digest(lockfile_bytes),
        )

    @classmethod
    def from_snapshot(cls, snapshot: WorkspaceSnapshot) -> WorkspaceAttestation:
        """Build a statement model from a guarded publication snapshot."""
        return cls.build(
            manifest_path=snapshot.manifest_name,
            manifest_bytes=snapshot.manifest_bytes,
            manifest_format=snapshot.manifest_format,
            lockfile_path=snapshot.lockfile_name,
            lockfile_bytes=snapshot.lockfile_bytes,
        )

    def to_statement(self) -> InTotoStatement:
        """Convert to conda-sigstore's generic validated statement."""
        InTotoStatement = _in_toto_statement_type()
        return InTotoStatement.from_payload(
            {
                "_type": InTotoStatement.STATEMENT_TYPE,
                "subject": [
                    {
                        "name": self.manifest_path,
                        "digest": {"sha256": self.manifest_sha256},
                    },
                    {
                        "name": self.lockfile_path,
                        "digest": {"sha256": self.lockfile_sha256},
                    },
                ],
                "predicateType": WORKSPACE_ATTESTATION_PREDICATE_TYPE,
                "predicate": {
                    "version": WORKSPACE_ATTESTATION_FORMAT_VERSION,
                    "workspace": {
                        "manifest": self.manifest_path,
                        "lockfile": self.lockfile_path,
                    },
                    "manifest": {"format": self.manifest_format},
                    "lockfile": {"format": LOCKFILE_FORMAT},
                },
            }
        )

    def payload(self) -> bytes:
        """Return stable statement bytes for signing."""
        return self.to_statement().payload()

    @classmethod
    def from_payload(cls, payload: bytes | str) -> WorkspaceAttestation:
        """Parse and strictly validate a workspace attestation payload."""
        try:
            statement = _in_toto_statement_type().from_payload(payload)
            return cls.from_statement(statement)
        except AttestationError:
            raise
        except Exception as exc:
            raise AttestationError(f"Invalid workspace attestation: {exc}") from exc

    @classmethod
    def from_statement(cls, statement: InTotoStatement) -> WorkspaceAttestation:
        """Validate all v1 workspace predicate fields and subjects."""
        value = statement.value
        if set(value) != {"_type", "subject", "predicateType", "predicate"}:
            raise AttestationError(
                "Workspace attestation statement contains unsupported fields."
            )
        if statement.predicate_type != WORKSPACE_ATTESTATION_PREDICATE_TYPE:
            raise AttestationError("Unsupported workspace attestation predicate type.")

        raw_subjects = value.get("subject")
        if not isinstance(raw_subjects, Sequence) or isinstance(
            raw_subjects,
            (str, bytes, bytearray),
        ):
            raise AttestationError("Workspace attestation subjects must be a list.")
        if len(raw_subjects) != 2:
            raise AttestationError(
                "Workspace attestation requires exactly two subjects."
            )
        subjects: list[tuple[str, str]] = []
        for raw_subject in raw_subjects:
            if not isinstance(raw_subject, Mapping) or set(raw_subject) != {
                "name",
                "digest",
            }:
                raise AttestationError(
                    "Workspace attestation subjects must contain only name and digest."
                )
            subject = cast("Mapping[str, object]", raw_subject)
            name = subject.get("name")
            digest = subject.get("digest")
            if not isinstance(name, str) or not isinstance(digest, Mapping):
                raise AttestationError("Workspace attestation subject is malformed.")
            subject_digest = cast("Mapping[str, object]", digest)
            if set(subject_digest) != {"sha256"}:
                raise AttestationError(
                    "Workspace attestation subject digest must contain only sha256."
                )
            subjects.append(
                (
                    name,
                    cls._validate_digest(
                        subject_digest.get("sha256"),
                        "subject sha256",
                    ),
                )
            )

        predicate = value.get("predicate")
        if not isinstance(predicate, Mapping) or set(predicate) != {
            "version",
            "workspace",
            "manifest",
            "lockfile",
        }:
            raise AttestationError("Workspace attestation predicate is malformed.")
        predicate_fields = cast("Mapping[str, object]", predicate)
        version = predicate_fields.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != WORKSPACE_ATTESTATION_FORMAT_VERSION
        ):
            raise AttestationError("Unsupported workspace attestation version.")

        workspace = predicate_fields.get("workspace")
        manifest = predicate_fields.get("manifest")
        lockfile = predicate_fields.get("lockfile")
        if not isinstance(workspace, Mapping) or set(workspace) != {
            "manifest",
            "lockfile",
        }:
            raise AttestationError(
                "Workspace attestation workspace paths are malformed."
            )
        if not isinstance(manifest, Mapping) or set(manifest) != {"format"}:
            raise AttestationError(
                "Workspace attestation manifest metadata is malformed."
            )
        if (
            not isinstance(lockfile, Mapping)
            or set(lockfile) != {"format"}
            or cast("Mapping[str, object]", lockfile).get("format") != LOCKFILE_FORMAT
        ):
            raise AttestationError("Unsupported workspace lockfile format.")

        workspace_paths = cast("Mapping[str, object]", workspace)
        manifest_metadata = cast("Mapping[str, object]", manifest)
        manifest_path = workspace_paths.get("manifest")
        lockfile_path = workspace_paths.get("lockfile")
        manifest_format = manifest_metadata.get("format")
        if (
            not isinstance(manifest_path, str)
            or not isinstance(lockfile_path, str)
            or not isinstance(manifest_format, str)
        ):
            raise AttestationError("Workspace attestation metadata is malformed.")
        if subjects[0][0] != manifest_path or subjects[1][0] != lockfile_path:
            raise AttestationError(
                "Workspace attestation subjects do not match the declared paths."
            )
        return cls(
            manifest_path=manifest_path,
            manifest_sha256=subjects[0][1],
            manifest_format=manifest_format,
            lockfile_path=lockfile_path,
            lockfile_sha256=subjects[1][1],
        )

    def verify_content(
        self,
        *,
        manifest_path: str,
        manifest_bytes: bytes,
        manifest_format: str,
        lockfile_path: str,
        lockfile_bytes: bytes,
    ) -> None:
        """Bind the authenticated statement to exact workspace bytes."""
        if (
            self.manifest_path != manifest_path
            or self.lockfile_path != lockfile_path
            or self.manifest_format != manifest_format
        ):
            raise AttestationError(
                "Workspace attestation paths or manifest format do not match."
            )
        if not hmac.compare_digest(
            self.manifest_sha256,
            self._digest(manifest_bytes),
        ):
            raise AttestationError(
                "Workspace attestation does not match the manifest bytes."
            )
        if not hmac.compare_digest(
            self.lockfile_sha256,
            self._digest(lockfile_bytes),
        ):
            raise AttestationError(
                "Workspace attestation does not match the lockfile bytes."
            )

    def verify_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        """Bind the authenticated statement to a guarded workspace snapshot."""
        self.verify_content(
            manifest_path=snapshot.manifest_name,
            manifest_bytes=snapshot.manifest_bytes,
            manifest_format=snapshot.manifest_format,
            lockfile_path=snapshot.lockfile_name,
            lockfile_bytes=snapshot.lockfile_bytes,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceVerification:
    """A verified workspace statement and its authenticated evidence."""

    attestation: WorkspaceAttestation
    evidence: VerifiedStatement
    authorized: bool | None

    @property
    def signer(self) -> SignerIdentity:
        return self.evidence.signer

    @property
    def timestamps(self) -> tuple[str, ...]:
        return tuple(self.evidence.timestamps)

    @property
    def predicate_type(self) -> str:
        return self.evidence.statement.predicate_type

    def require_authorized(self) -> None:
        """Reject verification where no matching receiver policy was applied."""
        if self.authorized is not True:
            raise AttestationError(
                "The authenticated Sigstore signer does not match the expected "
                "certificate identity and issuer."
            )


def default_attestation_path(input_path: Path) -> Path:
    """Return the default external Sigstore bundle path for an input."""
    return Path(f"{input_path}{SIGSTORE_JSON_SUFFIX}")


def read_attestation_bundle(
    path: Path,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Read one stable local bundle without following a symlink."""
    maximum_bytes = (
        _sigstore_settings().max_sidecar_bytes if max_bytes is None else max_bytes
    )
    try:
        bundle = read_regular_file_bytes(
            path,
            maximum_bytes=maximum_bytes,
            label="Sigstore bundle",
        )
        bundle.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AttestationError(f"Cannot read Sigstore bundle safely: {path}") from exc
    return bundle


def sign_attestation_payload(payload: bytes) -> str:
    """Sign a validated in-toto statement through conda-sigstore."""
    if conda_context.offline:
        raise AttestationError("Sigstore signing is unavailable in offline mode.")
    try:
        from conda_sigstore.attestation import sign_in_toto_statement

        settings = _sigstore_settings()
        statement = _in_toto_statement_type().from_payload(payload)
        return sign_in_toto_statement(
            statement,
            trust_config_path=settings.trust_config,
        )
    except AttestationError:
        raise
    except Exception as exc:
        raise AttestationError(f"Sigstore signing failed: {exc}") from exc


def verify_attestation_bundle(
    bundle_bytes: bytes,
) -> VerifiedStatement:
    """Return conda-sigstore's authenticated generic statement result."""
    try:
        from conda_sigstore.verification import SigstoreVerifier

        settings = _sigstore_settings()
        return SigstoreVerifier.shared(
            offline=conda_context.offline,
            trust_config=settings.trust_config,
        ).verify_statement(bundle_bytes.decode("utf-8"))
    except AttestationError:
        raise
    except Exception as exc:
        raise AttestationError(f"Sigstore verification failed: {exc}") from exc


def sign_workspace_snapshot(snapshot: WorkspaceSnapshot) -> str:
    """Sign one guarded workspace snapshot."""
    statement = WorkspaceAttestation.from_snapshot(snapshot)
    return sign_attestation_payload(statement.payload())


def verify_workspace_snapshot(
    bundle_bytes: bytes,
    snapshot: WorkspaceSnapshot,
    signer_policy: SignerPolicy | None,
) -> WorkspaceVerification:
    """Verify a bundle and bind it to one guarded workspace snapshot."""
    evidence = verify_attestation_bundle(bundle_bytes)
    attestation = WorkspaceAttestation.from_statement(evidence.statement)
    attestation.verify_snapshot(snapshot)
    return WorkspaceVerification(
        attestation=attestation,
        evidence=evidence,
        authorized=(
            None if signer_policy is None else signer_policy.authorize(evidence.signer)
        ),
    )


def _in_toto_statement_type() -> type[InTotoStatement]:
    try:
        from conda_sigstore.statements import InTotoStatement
    except ImportError as exc:
        raise AttestationError(_DEPENDENCY_MESSAGE) from exc
    return InTotoStatement


def _sigstore_settings() -> SigstoreSettings:
    try:
        from conda_sigstore.settings import SigstoreSettings

        return SigstoreSettings.current()
    except ImportError as exc:
        raise AttestationError(_DEPENDENCY_MESSAGE) from exc
    except ValueError as exc:
        raise AttestationError(f"Invalid conda-sigstore settings: {exc}") from exc
