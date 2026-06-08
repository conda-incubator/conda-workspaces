"""Small in-toto Statement receipts for workspace archives."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from conda_lockfiles.load_yaml import load_yaml

from .archive import (
    file_sha256,
    has_absolute_path_syntax,
    parse_relative_archive_path,
    url_to_filename,
)
from .exceptions import ArchiveError, ArchiveHashMismatchError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from typing import Any

    from .models import ArchiveConfig

IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
ARCHIVE_RECEIPT_PREDICATE_TYPE = (
    "https://conda-incubator.github.io/conda-workspaces/"
    "workspace-archive-receipt-1.schema.json"
)
ARCHIVE_RECEIPT_FORMAT_VERSION = 1

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

        archive_options = dict(options)
        archive_options.setdefault("include", list(archive_config.include))
        archive_options.setdefault("exclude", list(archive_config.exclude))
        archive_options.setdefault("compressionLevel", archive_config.compression_level)

        manifest_name = cls.archive_name(root, manifest_path)
        lockfile_name = cls.archive_name(root, lockfile_path)
        receipt = cls(
            {
                "_type": IN_TOTO_STATEMENT_TYPE,
                "subject": [
                    cls.file_subject(archive_path.name, archive_path),
                    cls.file_subject(manifest_name, manifest_path),
                    cls.file_subject(lockfile_name, lockfile_path),
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
                    "environments": ReceiptInventory.from_lockfile(
                        lockfile_path,
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

        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ArchiveError(f"Invalid receipt: duplicate JSON key '{key}'.")
                result[key] = value
            return result

        try:
            data = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=unique_object,
            )
        except OSError as exc:
            raise ArchiveError(f"Receipt not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ArchiveError(f"Invalid receipt JSON: {path}") from exc

        if not isinstance(data, dict):
            raise ArchiveError("Invalid receipt: expected a JSON object.")

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
        return {"name": name, "digest": {"sha256": file_sha256(path)}}

    def write(self, path: Path) -> Path:
        """Write the receipt as stable JSON."""
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.statement, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

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
        try:
            expected = self.subject_digests[name]
        except KeyError:
            raise ArchiveError(f"Receipt subject not found: {name}") from None
        try:
            actual = file_sha256(path)
        except OSError as exc:
            raise ArchiveError(f"Receipt subject file cannot be read: {name}") from exc
        if actual != expected:
            raise ArchiveHashMismatchError(name, expected=expected, actual=actual)

    def verify_archive(self, archive_path: Path) -> None:
        """Verify the archive digest before extraction."""
        self.verify_subject_file(archive_path.name, archive_path)

    def verify_extracted(
        self,
        extracted_dir: Path,
        *,
        require_sha256: bool = False,
    ) -> None:
        """Verify extracted manifest, lockfile, and lockfile package records."""
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

        self.verify_subject_file(manifest_name, manifest_path)
        self.verify_subject_file(lockfile_name, lockfile_path)

        expected = self.inventory
        actual = ReceiptInventory.from_lockfile(
            lockfile_path,
            environment_prefixes=expected.environment_names(),
        )
        expected.compare(actual, require_sha256=require_sha256)


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
        data = load_yaml(lockfile_path)
        if not isinstance(data, dict):
            raise ArchiveError("Invalid lockfile: expected a mapping.")
        lockfile_envs = data.get("environments") or {}
        if not isinstance(lockfile_envs, dict):
            raise ArchiveError("Invalid lockfile: environments must be a mapping.")
        packages_by_url = cls.packages_by_url(data.get("packages", []) or [])
        env_names = list(environment_prefixes or lockfile_envs)

        result: list[dict[str, object]] = []
        for env_name in env_names:
            env_data = lockfile_envs.get(env_name, {}) or {}
            if not isinstance(env_data, dict):
                raise ArchiveError("Invalid lockfile: environment must be a mapping.")
            platform_packages = env_data.get("packages", {}) or {}
            if not isinstance(platform_packages, dict):
                raise ArchiveError(
                    "Invalid lockfile: environment packages must be a mapping."
                )
            packages: list[dict[str, object]] = []
            for platform in sorted(platform_packages):
                refs = platform_packages[platform] or []
                if not isinstance(refs, list):
                    raise ArchiveError("Invalid lockfile package references.")
                for ref in refs:
                    if not isinstance(ref, dict):
                        raise ArchiveError("Invalid lockfile package reference.")
                    url = ReceiptPackageRecord.package_url(ref)
                    source = packages_by_url.get(url, ref)
                    packages.append(
                        ReceiptPackageRecord.from_record(
                            source,
                            fallback_url=url,
                            platform=platform,
                        ).data
                    )

            env: dict[str, object] = {
                "name": str(env_name),
                "packages": sorted(
                    packages,
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
                raise ArchiveError(f"Duplicate package URL in lockfile: {url}")
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
                    f"Missing package record for environment '{env_name}': {missing[0]}"
                )
            if unexpected := sorted(set(found) - set(expected)):
                raise ArchiveError(
                    f"Unexpected package record for environment"
                    f" '{env_name}': {unexpected[0]}"
                )
            for identity in sorted(expected):
                if require_sha256 and (
                    not expected[identity].get("sha256")
                    or not found[identity].get("sha256")
                ):
                    raise ArchiveError(
                        f"Package record '{identity}' in environment"
                        f" '{env_name}' lacks sha256."
                    )
                if expected[identity] != found[identity]:
                    raise ArchiveError(
                        f"Package record mismatch for environment"
                        f" '{env_name}': {identity}"
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
                    f"Duplicate package record for environment '{env_name}': {identity}"
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
            result["url"] = cls.redact_url(url)
            result.setdefault("fn", url_to_filename(url))
        if platform:
            result.setdefault("subdir", platform)

        channel = result.get("channel")
        if isinstance(channel, str) and channel:
            result["channel"] = cls.redact_url(channel)
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
    def redact_url(url: str) -> str:
        """Return *url* without credentials, tokens, query, or fragment."""
        from conda.common.url import remove_auth, split_anaconda_token

        redacted, _ = split_anaconda_token(url)
        redacted = remove_auth(redacted)
        parts = urlsplit(redacted)
        return parts._replace(query="", fragment="").geturl()

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
