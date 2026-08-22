"""Small in-toto Statement receipts for workspace archives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from .archive import (
    file_sha256,
    parse_relative_archive_path,
    url_to_filename,
)
from .exceptions import ArchiveError, ArchiveHashMismatchError
from .lockfile import MAX_LOCKFILE_BYTES, load_lockfile_data, load_lockfile_path
from .manifests.base import MAX_MANIFEST_BYTES
from .models import has_url_credentials, redact_url_text
from .parsing import decode_limited_text, read_limited_text, validate_document_limits
from .paths import (
    atomic_write_text,
    has_absolute_path_syntax,
    read_regular_file_bytes,
    regular_file_generation,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from typing import Any, Final

    from .models import ArchiveConfig
    from .paths import DirectoryIdentity, FileGeneration

_CURRENT_RECEIPT_GENERATION = object()

IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
ARCHIVE_RECEIPT_PREDICATE_TYPE = (
    "https://conda-incubator.github.io/conda-workspaces/"
    "workspace-archive-receipt-1.schema.json"
)
ARCHIVE_RECEIPT_FORMAT_VERSION = 1
MAX_RECEIPT_BYTES: Final = 64 * 1024**2
MAX_RECEIPT_DEPTH: Final = 128
MAX_RECEIPT_COLLECTION_ITEMS: Final = 100_000
MAX_RECEIPT_ITEMS: Final = 1_000_000

PACKAGE_RECORD_FIELDS = (
    "name",
    "version",
    "build",
    "build_number",
    "subdir",
    "channel",
    "url",
    "fn",
    "sha256",
    "md5",
)


@dataclass(frozen=True, slots=True)
class VerifiedArchiveWorkspace:
    """Exact workspace files accepted by archive receipt verification."""

    manifest_name: str
    manifest_bytes: bytes
    lockfile_name: str
    lockfile_bytes: bytes


@dataclass(frozen=True)
class ArchiveReceipt:
    """Unsigned receipt that binds an archive to workspace lockfile metadata."""

    statement: dict[str, Any]

    @classmethod
    def build(
        cls,
        *,
        root: Path,
        archive_path: Path,
        archive_config: ArchiveConfig,
        manifest_path: Path,
        lockfile_path: Path,
        environment_prefixes: Mapping[str, str | Path],
        options: dict[str, object],
    ) -> ArchiveReceipt:
        """Build a receipt for *archive_path* and the selected environments."""
        root = root.resolve()
        archive_path = archive_path.resolve()
        manifest_path = manifest_path.resolve()
        lockfile_path = lockfile_path.resolve()

        if not manifest_path.is_file():
            raise ArchiveError(
                "Cannot write receipt: workspace manifest was not found."
            )
        if not lockfile_path.is_file():
            raise ArchiveError(
                "Cannot write receipt: no conda.lock found.",
                hints=["Run 'conda workspace lock' first."],
            )

        manifest_name = cls.archive_name(root, manifest_path)
        lockfile_name = cls.archive_name(root, lockfile_path)
        lockfile_content = read_regular_file_bytes(
            lockfile_path,
            maximum_bytes=MAX_LOCKFILE_BYTES,
            label="Workspace lockfile",
        )
        return cls.build_from_captured(
            archive_name=archive_path.name,
            archive_sha256=file_sha256(archive_path),
            manifest_name=manifest_name,
            manifest_sha256=file_sha256(manifest_path),
            lockfile_name=lockfile_name,
            lockfile_sha256=hashlib.sha256(lockfile_content).hexdigest(),
            lockfile_data=load_lockfile_data(lockfile_content),
            archive_config=archive_config,
            environment_prefixes=environment_prefixes,
            options=options,
        )

    @classmethod
    def build_from_captured(
        cls,
        *,
        archive_name: str,
        archive_sha256: str,
        manifest_name: str,
        manifest_sha256: str,
        lockfile_name: str,
        lockfile_sha256: str,
        lockfile_data: object,
        archive_config: ArchiveConfig,
        environment_prefixes: Mapping[str, str | Path],
        options: dict[str, object],
    ) -> ArchiveReceipt:
        """Build a receipt from the exact inputs captured for an archive."""
        archive_options = dict(options)
        archive_options.setdefault("include", list(archive_config.include))
        archive_options.setdefault("exclude", list(archive_config.exclude))
        archive_options.setdefault("compressionLevel", archive_config.compression_level)

        receipt = cls(
            {
                "_type": IN_TOTO_STATEMENT_TYPE,
                "subject": [
                    cls.digest_subject(archive_name, archive_sha256),
                    cls.digest_subject(manifest_name, manifest_sha256),
                    cls.digest_subject(lockfile_name, lockfile_sha256),
                ],
                "predicateType": ARCHIVE_RECEIPT_PREDICATE_TYPE,
                "predicate": {
                    "archive": {
                        "formatVersion": ARCHIVE_RECEIPT_FORMAT_VERSION,
                        "options": archive_options,
                    },
                    "workspace": {
                        "manifest": manifest_name,
                        "lockfile": lockfile_name,
                    },
                    "environments": ReceiptInventory.from_lockfile_data(
                        lockfile_data,
                        environment_prefixes=environment_prefixes,
                    ).data,
                },
            }
        )
        receipt.validate()
        return receipt

    @classmethod
    def load(cls, path: Path) -> ArchiveReceipt:
        """Load a receipt JSON file, rejecting ambiguous duplicate keys."""
        try:
            content = read_limited_text(
                path,
                maximum_bytes=MAX_RECEIPT_BYTES,
                label="Receipt JSON",
            )
        except OSError as exc:
            raise ArchiveError(f"Receipt not found: {path}") from exc
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise ArchiveError(f"Invalid receipt: {exc}") from exc
        return cls._from_text(content, invalid_json=f"Invalid receipt JSON: {path}")

    @classmethod
    def from_payload(cls, payload: bytes | str) -> ArchiveReceipt:
        """Parse a verified in-toto payload as an archive receipt."""
        try:
            content = decode_limited_text(
                payload,
                maximum_bytes=MAX_RECEIPT_BYTES,
                label="Receipt payload",
            )
        except (UnicodeDecodeError, UnicodeEncodeError, ValueError) as exc:
            raise ArchiveError(f"Invalid receipt: {exc}") from exc
        return cls._from_text(content, invalid_json="Invalid receipt payload JSON.")

    @classmethod
    def _from_text(cls, content: str, *, invalid_json: str) -> ArchiveReceipt:
        """Parse one size-bounded receipt document."""

        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ArchiveError(f"Invalid receipt: duplicate JSON key '{key}'.")
                result[key] = value
            return result

        try:
            data = json.loads(content, object_pairs_hook=unique_object)
        except json.JSONDecodeError as exc:
            raise ArchiveError(invalid_json) from exc
        except RecursionError as exc:
            raise ArchiveError(f"Invalid receipt: {exc}") from exc

        if not isinstance(data, dict):
            raise ArchiveError("Invalid receipt: expected a JSON object.")
        try:
            validate_document_limits(
                data,
                label="Receipt JSON",
                maximum_depth=MAX_RECEIPT_DEPTH,
                maximum_collection_items=MAX_RECEIPT_COLLECTION_ITEMS,
                maximum_items=MAX_RECEIPT_ITEMS,
            )
        except ValueError as exc:
            raise ArchiveError(f"Invalid receipt: {exc}") from exc

        receipt = cls(cast("dict[str, Any]", data))
        receipt.validate()
        return receipt

    @staticmethod
    def default_path(archive_path: Path) -> Path:
        """Return the default external receipt path for *archive_path*."""
        return archive_path.with_name(f"{archive_path.name}.receipt.json")

    @staticmethod
    def archive_name(root: Path, path: Path) -> str:
        """Return *path* as a POSIX path relative to *root* when possible."""
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.name

    @staticmethod
    def file_subject(name: str, path: Path) -> dict[str, object]:
        """Return an in-toto subject for a file."""
        return ArchiveReceipt.digest_subject(name, file_sha256(path))

    @staticmethod
    def digest_subject(name: str, sha256: str) -> dict[str, object]:
        """Return an in-toto subject for an already captured SHA-256 digest."""
        return {"name": name, "digest": {"sha256": sha256}}

    def write(
        self,
        path: Path,
        *,
        expected_generation: FileGeneration | None | object = (
            _CURRENT_RECEIPT_GENERATION
        ),
        expected_parent_identity: DirectoryIdentity | None = None,
        capture_generation: Callable[[FileGeneration], None] | None = None,
    ) -> Path:
        """Write the receipt as stable JSON."""
        if path.is_symlink():
            raise ArchiveError("Receipt output cannot be a symbolic link.")
        if expected_generation is _CURRENT_RECEIPT_GENERATION:
            expected_generation = regular_file_generation(path)
        self.validate()
        if path.is_symlink():
            raise ArchiveError("Receipt output cannot be a symbolic link.")
        if expected_parent_identity is None:
            atomic_write_text(
                path,
                self.serialized_text(),
                expected_generation=expected_generation,
                capture_generation=capture_generation,
            )
        else:
            atomic_write_text(
                path,
                self.serialized_text(),
                expected_generation=expected_generation,
                expected_parent_identity=expected_parent_identity,
                capture_generation=capture_generation,
            )
        return path

    def serialized_text(self) -> str:
        """Return the stable JSON representation written for this receipt."""
        return json.dumps(self.statement, indent=2, sort_keys=True) + "\n"

    def validate(self) -> None:
        """Validate only the receipt fields used by integrity verification."""
        if self.statement.get("_type") != IN_TOTO_STATEMENT_TYPE:
            raise ArchiveError("Invalid receipt: unsupported in-toto statement type.")
        if self.statement.get("predicateType") != ARCHIVE_RECEIPT_PREDICATE_TYPE:
            raise ArchiveError("Invalid receipt: unsupported predicate type.")
        self.subject_digests
        self.workspace_paths
        self.format_version
        self.inventory

    @property
    def predicate(self) -> Mapping[str, object]:
        """Return the Statement predicate object."""
        value = self.statement.get("predicate")
        if not isinstance(value, dict):
            raise ArchiveError("Invalid receipt: predicate must be an object.")
        return cast("Mapping[str, object]", value)

    @property
    def format_version(self) -> int:
        """Return the supported predicate format version."""
        archive = self.predicate.get("archive")
        if not isinstance(archive, dict):
            raise ArchiveError("Invalid receipt: predicate.archive must be an object.")
        archive_data = cast("Mapping[str, object]", archive)
        value = archive_data.get("formatVersion")
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value != ARCHIVE_RECEIPT_FORMAT_VERSION
        ):
            raise ArchiveError("Invalid receipt: unsupported archive format version.")
        return value

    @property
    def workspace_paths(self) -> tuple[str, str]:
        """Return validated archive-relative manifest and lockfile paths."""
        workspace = self.predicate.get("workspace")
        if not isinstance(workspace, dict):
            raise ArchiveError(
                "Invalid receipt: predicate.workspace must be an object."
            )
        workspace_data = cast("Mapping[str, object]", workspace)
        manifest = workspace_data.get("manifest")
        lockfile = workspace_data.get("lockfile")
        if not isinstance(manifest, str) or not isinstance(lockfile, str):
            raise ArchiveError("Invalid receipt: workspace paths are missing.")
        return (
            self.relative_archive_path(manifest, "workspace manifest"),
            self.relative_archive_path(lockfile, "workspace lockfile"),
        )

    @property
    def subject_digests(self) -> dict[str, str]:
        """Return receipt subjects keyed by name."""
        subjects = self.statement.get("subject")
        if not isinstance(subjects, list) or not subjects:
            raise ArchiveError("Invalid receipt: subject must be a non-empty list.")

        result: dict[str, str] = {}
        for subject in subjects:
            if not isinstance(subject, dict):
                raise ArchiveError("Invalid receipt: subject entries must be objects.")
            subject_data = cast("Mapping[str, object]", subject)
            name = subject_data.get("name")
            digest = subject_data.get("digest")
            if not isinstance(name, str) or not name:
                raise ArchiveError("Invalid receipt: subject entry is missing a name.")
            if name in result:
                raise ArchiveError("Invalid receipt: duplicate subject name.")
            if not isinstance(digest, dict):
                raise ArchiveError(
                    "Invalid receipt: subject entry is missing a sha256 digest."
                )
            digest_data = cast("Mapping[str, object]", digest)
            if not isinstance(digest_data.get("sha256"), str):
                raise ArchiveError(
                    "Invalid receipt: subject entry is missing a sha256 digest."
                )
            result[name] = self.sha256_digest(str(digest_data["sha256"]))
        return result

    @property
    def inventory(self) -> ReceiptInventory:
        """Return package inventory recorded in the predicate."""
        environments = self.predicate.get("environments")
        if not isinstance(environments, list):
            raise ArchiveError("Invalid receipt: environments must be a list.")

        records: list[dict[str, object]] = []
        for index, value in enumerate(environments):
            records.append(self.environment_record(value, index))
        inventory = ReceiptInventory(records)
        for env_name, env in inventory.index_environments().items():
            ReceiptInventory.index_packages(env, env_name)
        return inventory

    @classmethod
    def environment_record(cls, value: object, index: int) -> dict[str, object]:
        """Parse one receipt environment record."""
        if not isinstance(value, dict):
            raise ArchiveError(
                f"Invalid receipt: environment entry {index} is malformed."
            )
        env = cast("Mapping[str, object]", value)
        name = env.get("name")
        packages = env.get("packages")
        if not isinstance(name, str) or not name or not isinstance(packages, list):
            raise ArchiveError("Invalid receipt: environment entry is malformed.")

        result: dict[str, object] = {
            "name": name,
            "packages": [
                ReceiptPackageRecord.parse(package).data for package in packages
            ],
        }
        prefix = env.get("prefix")
        if prefix is not None:
            if not isinstance(prefix, str):
                raise ArchiveError("Invalid receipt: environment prefix is malformed.")
            if prefix and not has_absolute_path_syntax(prefix):
                prefix = cls.relative_archive_path(prefix, f"environment '{name}'")
            result["prefix"] = prefix
        return result

    @classmethod
    def relative_archive_path(cls, path: str, field: str) -> str:
        """Return a validated relative POSIX archive path."""
        try:
            parse_relative_archive_path(path)
        except ValueError:
            raise ArchiveError(
                f"Invalid receipt: {field} path must be a relative archive path."
            ) from None
        return path

    @classmethod
    def path_under(cls, root: Path, path: str, field: str) -> Path:
        """Resolve receipt path *path* under *root*, rejecting symlink escapes."""
        try:
            relative = parse_relative_archive_path(path)
        except ValueError:
            raise ArchiveError(
                f"Invalid receipt: {field} path must be a relative archive path."
            ) from None
        root = root.resolve()
        candidate = root.joinpath(*relative.parts).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ArchiveError(
                f"Invalid receipt: {field} path escapes the extraction target."
            )
        return candidate

    @staticmethod
    def sha256_digest(value: str) -> str:
        """Return a lowercase SHA-256 digest string."""
        try:
            digest = bytes.fromhex(value)
        except ValueError:
            raise ArchiveError("Invalid receipt: invalid sha256 digest.") from None
        if len(value) != 64 or len(digest) != 32:
            raise ArchiveError("Invalid receipt: invalid sha256 digest.")
        return value.lower()

    def verify_subject_file(self, name: str, path: Path) -> None:
        """Verify *path* against the named receipt subject."""
        display_name = self.subject_display_name(name)
        try:
            actual = file_sha256(path)
        except (OSError, ArchiveError) as exc:
            raise ArchiveError(
                f"Receipt subject file cannot be read: {display_name}"
            ) from exc
        self.verify_subject_digest(name, actual)

    def verified_subject_bytes(
        self,
        name: str,
        path: Path,
        *,
        maximum_bytes: int,
        label: str,
    ) -> bytes:
        """Capture and verify one bounded subject file without reopening it."""
        display_name = self.subject_display_name(name)
        try:
            content = read_regular_file_bytes(
                path,
                maximum_bytes=maximum_bytes,
                label=label,
            )
        except ValueError as exc:
            raise ArchiveError(
                f"Receipt subject file cannot be read: {display_name}"
            ) from exc
        self.verify_subject_digest(name, hashlib.sha256(content).hexdigest())
        return content

    def verify_subject_digest(self, name: str, actual: str) -> None:
        """Verify an already captured digest against the named receipt subject."""
        display_name = self.subject_display_name(name)
        try:
            expected = self.subject_digests[name]
        except KeyError:
            raise ArchiveError(f"Receipt subject not found: {display_name}") from None
        if actual != expected:
            raise ArchiveHashMismatchError(
                display_name,
                expected=expected,
                actual=actual,
            )

    @staticmethod
    def subject_display_name(name: str) -> str:
        """Return a receipt subject name safe to include in diagnostics."""
        return "<redacted-path>" if has_url_credentials(name) else redact_url_text(name)

    def verify_archive(self, archive_path: Path) -> None:
        """Verify the archive digest before extraction."""
        self.verify_subject_file(archive_path.name, archive_path)

    def verify_extracted(
        self,
        extracted_dir: Path,
        *,
        require_sha256: bool = False,
    ) -> VerifiedArchiveWorkspace:
        """Verify and return the exact extracted workspace subject bytes."""
        manifest_name, lockfile_name = self.workspace_paths
        manifest_path = self.path_under(
            extracted_dir,
            manifest_name,
            "workspace manifest",
        )
        lockfile_path = self.path_under(
            extracted_dir,
            lockfile_name,
            "workspace lockfile",
        )

        manifest_content = self.verified_subject_bytes(
            manifest_name,
            manifest_path,
            maximum_bytes=MAX_MANIFEST_BYTES,
            label="Workspace manifest",
        )
        lockfile_content = self.verified_subject_bytes(
            lockfile_name,
            lockfile_path,
            maximum_bytes=MAX_LOCKFILE_BYTES,
            label="Workspace lockfile",
        )

        expected = self.inventory
        try:
            lockfile_data = load_lockfile_data(lockfile_content)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ArchiveError("Invalid extracted workspace lockfile.") from None
        actual = ReceiptInventory.from_lockfile_data(
            lockfile_data,
            environment_prefixes=expected.environment_names(),
        )
        expected.compare(actual, require_sha256=require_sha256)
        return VerifiedArchiveWorkspace(
            manifest_name=manifest_name,
            manifest_bytes=manifest_content,
            lockfile_name=lockfile_name,
            lockfile_bytes=lockfile_content,
        )


@dataclass(frozen=True)
class ReceiptInventory:
    """Package records grouped by workspace environment."""

    data: list[dict[str, object]]

    @classmethod
    def from_lockfile(
        cls,
        lockfile_path: Path,
        *,
        environment_prefixes: Mapping[str, str | Path] | None = None,
    ) -> ReceiptInventory:
        """Return receipt-ready package inventory from ``conda.lock``."""
        data = load_lockfile_path(lockfile_path)
        return cls.from_lockfile_data(
            data,
            environment_prefixes=environment_prefixes,
        )

    @classmethod
    def from_lockfile_data(
        cls,
        data: object,
        *,
        environment_prefixes: Mapping[str, str | Path] | None = None,
    ) -> ReceiptInventory:
        """Return receipt-ready inventory from parsed lockfile *data*."""
        if not isinstance(data, dict):
            raise ArchiveError("Invalid lockfile: expected a mapping.")
        data = cast("dict[str, object]", data)
        lockfile_envs = data.get("environments") or {}
        if not isinstance(lockfile_envs, dict):
            raise ArchiveError("Invalid lockfile: environments must be a mapping.")
        lockfile_envs = cast("dict[str, object]", lockfile_envs)
        packages_by_url = cls.packages_by_url(data.get("packages", []) or [])
        env_names = list(environment_prefixes or lockfile_envs)

        result: list[dict[str, object]] = []
        for env_name in env_names:
            env_data = lockfile_envs.get(env_name, {}) or {}
            if not isinstance(env_data, dict):
                raise ArchiveError("Invalid lockfile: environment must be a mapping.")
            env_data = cast("dict[str, object]", env_data)
            platform_packages = env_data.get("packages", {}) or {}
            if not isinstance(platform_packages, dict):
                raise ArchiveError(
                    "Invalid lockfile: environment packages must be a mapping."
                )
            platform_packages = cast("dict[str, object]", platform_packages)
            packages_by_identity: dict[str, dict[str, object]] = {}
            for platform in sorted(platform_packages):
                refs = platform_packages[platform] or []
                if not isinstance(refs, list):
                    raise ArchiveError("Invalid lockfile package references.")
                for ref in refs:
                    if not isinstance(ref, dict):
                        raise ArchiveError("Invalid lockfile package reference.")
                    ref = cast("dict[str, object]", ref)
                    url = ReceiptPackageRecord.package_url(ref)
                    source = packages_by_url.get(url, ref)
                    package = ReceiptPackageRecord.from_record(
                        source,
                        fallback_url=url,
                        platform=platform,
                    )
                    existing = packages_by_identity.setdefault(
                        package.identity,
                        package.data,
                    )
                    if existing != package.data:
                        raise ArchiveError(
                            "Duplicate package record for environment"
                            f" '{env_name}': {redact_url_text(package.identity)}"
                        )

            env: dict[str, object] = {
                "name": str(env_name),
                "packages": sorted(
                    packages_by_identity.values(),
                    key=lambda record: ReceiptPackageRecord(record).identity,
                ),
            }
            if environment_prefixes and env_name in environment_prefixes:
                env["prefix"] = str(environment_prefixes[env_name])
            result.append(env)

        inventory = cls(result)
        inventory.index_environments()
        return inventory

    @staticmethod
    def packages_by_url(records: object) -> dict[str, dict[str, object]]:
        """Return top-level lockfile package records keyed by package URL."""
        result: dict[str, dict[str, object]] = {}
        if not isinstance(records, list):
            return result
        for record in records:
            if not isinstance(record, dict):
                continue
            record_data = cast("dict[str, object]", record)
            url = ReceiptPackageRecord.package_url(record_data)
            if not url:
                continue
            if url in result:
                raise ArchiveError(
                    f"Duplicate package URL in lockfile: {redact_url_text(url)}"
                )
            result[url] = record_data
        return result

    def environment_names(self) -> dict[str, str]:
        """Return environment names as a mapping for lockfile inventory loading."""
        return {str(env["name"]): "" for env in self.data}

    def index_environments(self) -> dict[str, dict[str, object]]:
        """Return environments keyed by name, rejecting duplicate names."""
        result: dict[str, dict[str, object]] = {}
        for env in self.data:
            name = env.get("name")
            if not isinstance(name, str) or not name:
                raise ArchiveError("Invalid receipt: environment entry is malformed.")
            if name in result:
                raise ArchiveError(f"Duplicate environment record: {name}")
            result[name] = env
        return result

    def compare(
        self,
        actual: ReceiptInventory,
        *,
        require_sha256: bool = False,
    ) -> None:
        """Raise if *actual* does not match this inventory."""
        expected_envs = self.index_environments()
        actual_envs = actual.index_environments()
        if missing := sorted(set(expected_envs) - set(actual_envs)):
            raise ArchiveError(f"Missing environment record: {missing[0]}")
        if unexpected := sorted(set(actual_envs) - set(expected_envs)):
            raise ArchiveError(f"Unexpected environment record: {unexpected[0]}")

        for env_name in sorted(expected_envs):
            expected = self.index_packages(expected_envs[env_name], env_name)
            found = self.index_packages(actual_envs[env_name], env_name)
            if missing := sorted(set(expected) - set(found)):
                raise ArchiveError(
                    f"Missing package record for environment '{env_name}':"
                    f" {redact_url_text(missing[0])}"
                )
            if unexpected := sorted(set(found) - set(expected)):
                raise ArchiveError(
                    f"Unexpected package record for environment"
                    f" '{env_name}': {redact_url_text(unexpected[0])}"
                )
            for identity in sorted(expected):
                if require_sha256 and (
                    not expected[identity].get("sha256")
                    or not found[identity].get("sha256")
                ):
                    raise ArchiveError(
                        f"Package record '{redact_url_text(identity)}' in environment"
                        f" '{env_name}' lacks sha256."
                    )
                if expected[identity] != found[identity]:
                    raise ArchiveError(
                        f"Package record mismatch for environment"
                        f" '{env_name}': {redact_url_text(identity)}"
                    )

    @staticmethod
    def index_packages(
        env: dict[str, object],
        env_name: str,
    ) -> dict[str, dict[str, object]]:
        """Return package records keyed by identity, rejecting duplicates."""
        packages = env.get("packages")
        if not isinstance(packages, list):
            raise ArchiveError(
                f"Invalid package inventory for environment '{env_name}'."
            )
        result: dict[str, dict[str, object]] = {}
        for package in packages:
            if not isinstance(package, dict):
                raise ArchiveError(
                    f"Invalid package inventory for environment '{env_name}'."
                )
            record = ReceiptPackageRecord(cast("dict[str, object]", package))
            identity = record.identity
            if not identity:
                raise ArchiveError(
                    f"Invalid package inventory for environment '{env_name}'."
                )
            if identity in result:
                raise ArchiveError(
                    f"Duplicate package record for environment '{env_name}':"
                    f" {redact_url_text(identity)}"
                )
            result[identity] = record.data
        return result


@dataclass(frozen=True)
class ReceiptPackageRecord:
    """Receipt-ready view of a lockfile package record."""

    data: dict[str, object]

    @classmethod
    def parse(cls, value: object) -> ReceiptPackageRecord:
        """Parse a package record from receipt JSON."""
        if not isinstance(value, dict):
            raise ArchiveError("Invalid receipt: package record must be an object.")
        source = cast("Mapping[str, object]", value)
        record = {
            field: source[field]
            for field in PACKAGE_RECORD_FIELDS
            if field in source and source[field] is not None
        }
        for field, field_value in record.items():
            if field == "build_number":
                if not (
                    isinstance(field_value, str)
                    or (
                        isinstance(field_value, int)
                        and not isinstance(field_value, bool)
                    )
                ):
                    raise ArchiveError(
                        "Invalid receipt: package build_number is malformed."
                    )
            elif not isinstance(field_value, str):
                raise ArchiveError(f"Invalid receipt: package {field} is malformed.")
            elif field in {"channel", "url"}:
                record[field] = redact_url_text(field_value)
        for field, length in (("sha256", 64), ("md5", 32)):
            if field in record:
                cls.hex_digest(str(record[field]), length, field)
                record[field] = str(record[field]).lower()

        package = cls(record)
        if not package.identity:
            raise ArchiveError("Invalid receipt: package record lacks an identity.")
        return package

    @classmethod
    def from_record(
        cls,
        record: Mapping[str, object],
        *,
        fallback_url: str = "",
        platform: str = "",
    ) -> ReceiptPackageRecord:
        """Normalize a lockfile package record for receipts."""
        result = {
            field: record[field]
            for field in PACKAGE_RECORD_FIELDS
            if field in record and record[field] is not None
        }
        url = cls.package_url(record) or fallback_url
        if url:
            result["url"] = redact_url_text(url)
            result.setdefault("fn", url_to_filename(url))
        if "subdir" not in result and url and isinstance(result.get("fn"), str):
            subdir = cls.url_subdir(str(result["url"]), str(result["fn"]))
            if subdir:
                result["subdir"] = subdir
        if platform:
            result.setdefault("subdir", platform)

        channel = result.get("channel")
        if isinstance(channel, str) and channel:
            result["channel"] = redact_url_text(channel)
        elif url and isinstance(result.get("fn"), str):
            result["channel"] = cls.channel_url(
                str(result["url"]),
                str(result.get("subdir", "")),
                str(result["fn"]),
            )
        return cls.parse(result)

    @staticmethod
    def package_url(record: Mapping[str, object]) -> str:
        """Return a lockfile package URL."""
        value = record.get("conda") or record.get("url")
        return value if isinstance(value, str) else ""

    @staticmethod
    def channel_url(url: str, subdir: str, filename: str) -> str:
        """Derive a channel URL from a package artifact URL."""
        parts = urlsplit(url)
        path = parts.path
        suffix = f"/{subdir}/{filename}"
        if subdir and filename and path.endswith(suffix):
            path = path[: -len(suffix)]
        elif filename and path.endswith(f"/{filename}"):
            path = path[: -(len(filename) + 1)]
        return parts._replace(path=path).geturl()

    @staticmethod
    def url_subdir(url: str, filename: str) -> str:
        """Derive a package subdir from an artifact URL."""
        path = urlsplit(url).path
        suffix = f"/{filename}"
        if not filename or not path.endswith(suffix):
            return ""
        parent = path[: -len(suffix)].rstrip("/")
        return parent.rsplit("/", 1)[-1] if parent else ""

    @staticmethod
    def hex_digest(value: str, length: int, field: str) -> None:
        """Validate a lowercase hex digest."""
        try:
            digest = bytes.fromhex(value)
        except ValueError:
            raise ArchiveError(
                f"Invalid receipt: package {field} is malformed."
            ) from None
        if len(value) != length or len(digest) != length // 2:
            raise ArchiveError(f"Invalid receipt: package {field} is malformed.")

    @property
    def identity(self) -> str:
        """Return the comparison identity for this package record."""
        if url := self.data.get("url"):
            return str(url)
        if filename := self.data.get("fn"):
            subdir = str(self.data.get("subdir", ""))
            return f"{subdir}/{filename}" if subdir else str(filename)
        parts = [
            str(self.data.get("name", "")),
            str(self.data.get("version", "")),
            str(self.data.get("build", "")),
            str(self.data.get("channel", "")),
        ]
        return "|".join(parts) if any(parts) else ""
