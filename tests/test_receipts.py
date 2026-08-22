from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

import conda_workspaces.receipts as receipts_module
from conda_workspaces.archive import create_archive, extract_archive
from conda_workspaces.exceptions import ArchiveError
from conda_workspaces.models import ArchiveConfig
from conda_workspaces.receipts import (
    ARCHIVE_RECEIPT_PREDICATE_TYPE,
    IN_TOTO_STATEMENT_TYPE,
    ArchiveReceipt,
    ReceiptInventory,
    ReceiptPackageRecord,
    VerifiedArchiveWorkspace,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Any


def write_lockfile(root: Path, *, sha256: bool = True) -> None:
    sha256_line = (
        "    sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        if sha256
        else ""
    )
    root.joinpath("conda.lock").write_text(
        f"""\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://user:pass@conda.anaconda.org/t/token/conda-forge/linux-64/zlib-1.2.13-h4dc568a_6.conda?token=query
packages:
  - conda: https://user:pass@conda.anaconda.org/t/token/conda-forge/linux-64/zlib-1.2.13-h4dc568a_6.conda?token=query
{sha256_line}    md5: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    name: zlib
    version: 1.2.13
    build: h4dc568a_6
    subdir: linux-64
    depends: []
""",
        encoding="utf-8",
    )


@pytest.fixture
def receipt_workspace(tmp_path: Path) -> Path:
    tmp_path.joinpath("conda.toml").write_text(
        "[workspace]\nname = 'receipt-test'\n",
        encoding="utf-8",
    )
    write_lockfile(tmp_path)
    tmp_path.joinpath("src").mkdir()
    tmp_path.joinpath("src", "app.py").write_text("print('hi')\n", encoding="utf-8")
    return tmp_path


def build_receipt(root: Path, archive_path: Path) -> ArchiveReceipt:
    return ArchiveReceipt.build(
        root=root,
        archive_path=archive_path,
        archive_config=ArchiveConfig(),
        manifest_path=root / "conda.toml",
        lockfile_path=root / "conda.lock",
        environment_prefixes={"default": ".conda/envs/default"},
        options={"bundle": False, "lock": False},
    )


def copied_statement(receipt: ArchiveReceipt) -> dict[str, Any]:
    return json.loads(json.dumps(receipt.statement))


def test_archive_receipt_roundtrip(receipt_workspace: Path, tmp_path: Path) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    receipt_path = ArchiveReceipt.default_path(archive_path)

    receipt = build_receipt(receipt_workspace, archive_path)
    receipt.write(receipt_path)

    loaded = ArchiveReceipt.load(receipt_path)
    loaded.verify_archive(archive_path)
    target = tmp_path / "extracted"
    extract_archive(archive_path, target)
    loaded.verify_extracted(target)

    assert loaded.statement["_type"] == IN_TOTO_STATEMENT_TYPE
    assert loaded.statement["predicateType"] == ARCHIVE_RECEIPT_PREDICATE_TYPE
    package = loaded.statement["predicate"]["environments"][0]["packages"][0]
    assert package["url"] == (
        "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.2.13-h4dc568a_6.conda"
    )
    assert "user:pass" not in json.dumps(loaded.statement)
    assert "/t/token/" not in json.dumps(loaded.statement)


@pytest.mark.parametrize("payload_type", ["bytes", "text"], ids=["bytes", "text"])
def test_archive_receipt_from_verified_payload(
    receipt_workspace: Path,
    tmp_path: Path,
    payload_type: str,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    expected = build_receipt(receipt_workspace, archive_path)
    payload = expected.serialized_text()

    actual = ArchiveReceipt.from_payload(
        payload.encode("utf-8") if payload_type == "bytes" else payload
    )

    assert actual.statement == expected.statement
    actual.verify_archive(archive_path)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"", "Invalid receipt payload JSON"),
        (b"[]", "expected a JSON object"),
        (
            b'{"_type":"https://in-toto.io/Statement/v1","_type":"x"}',
            "duplicate JSON key",
        ),
    ],
    ids=["empty", "non-object", "duplicate-key"],
)
def test_archive_receipt_from_payload_rejects_invalid_statement(
    payload: bytes,
    match: str,
) -> None:
    with pytest.raises(ArchiveError, match=match):
        ArchiveReceipt.from_payload(payload)


@pytest.mark.parametrize(
    ("mutation", "preserve_generation"),
    [
        ("replace", False),
        ("rewrite", False),
        ("rewrite", True),
    ],
    ids=["replace", "rewrite", "same-generation-rewrite"],
)
def test_archive_receipt_rejects_changed_validated_output(
    receipt_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    preserve_generation: bool,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    receipt_path = ArchiveReceipt.default_path(archive_path)
    original_content = b"existing receipt"
    concurrent_content = b"changed receipt!"
    receipt_path.write_bytes(original_content)
    receipt = build_receipt(receipt_workspace, archive_path)
    original_atomic_write_text = receipts_module.atomic_write_text

    def mutate_before_write(path: Path, content: str, **kwargs: Any) -> None:
        if mutation == "replace":
            replacement = receipt_path.with_name("replacement.receipt.json")
            replacement.write_bytes(concurrent_content)
            replacement.replace(receipt_path)
        else:
            receipt_path.write_bytes(concurrent_content)
        if preserve_generation:
            kwargs["expected_generation"] = receipts_module.regular_file_generation(
                receipt_path
            )
        original_atomic_write_text(path, content, **kwargs)

    monkeypatch.setattr(
        receipts_module,
        "atomic_write_text",
        mutate_before_write,
    )

    with pytest.raises(ValueError, match="changed before writing"):
        receipt.write(receipt_path)

    assert len(concurrent_content) == len(original_content)
    assert receipt_path.read_bytes() == concurrent_content


@pytest.mark.parametrize("mutation", ["replace", "rewrite"])
def test_archive_receipt_captures_output_generation_before_validation(
    receipt_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    receipt_path = ArchiveReceipt.default_path(archive_path)
    receipt_path.write_text("existing receipt", encoding="utf-8")
    concurrent_content = "concurrent receipt generation"
    receipt = build_receipt(receipt_workspace, archive_path)
    original_validate = ArchiveReceipt.validate

    def mutate_during_validation(self: ArchiveReceipt) -> None:
        original_validate(self)
        if mutation == "replace":
            replacement = receipt_path.with_name("replacement.receipt.json")
            replacement.write_text(concurrent_content, encoding="utf-8")
            replacement.replace(receipt_path)
        else:
            receipt_path.write_text(concurrent_content, encoding="utf-8")

    monkeypatch.setattr(ArchiveReceipt, "validate", mutate_during_validation)

    with pytest.raises(ValueError, match="changed before writing"):
        receipt.write(receipt_path)

    assert receipt_path.read_text(encoding="utf-8") == concurrent_content


def test_archive_receipt_deduplicates_noarch_packages_across_platforms(
    receipt_workspace: Path,
    tmp_path: Path,
) -> None:
    receipt_workspace.joinpath("conda.lock").write_text(
        """\
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/noarch/pan-1.3.1-pyhd8ed1ab_0.conda
      osx-arm64:
        - conda: https://conda.anaconda.org/conda-forge/noarch/pan-1.3.1-pyhd8ed1ab_0.conda
packages:
  - conda: https://conda.anaconda.org/conda-forge/noarch/pan-1.3.1-pyhd8ed1ab_0.conda
    sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    md5: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    name: pan
    version: 1.3.1
    build: pyhd8ed1ab_0
    depends: []
""",
        encoding="utf-8",
    )
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())

    receipt = build_receipt(receipt_workspace, archive_path)

    packages = receipt.statement["predicate"]["environments"][0]["packages"]
    assert packages == [
        {
            "build": "pyhd8ed1ab_0",
            "channel": "https://conda.anaconda.org/conda-forge",
            "fn": "pan-1.3.1-pyhd8ed1ab_0.conda",
            "md5": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "name": "pan",
            "sha256": (
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ),
            "subdir": "noarch",
            "url": "https://conda.anaconda.org/conda-forge/noarch/pan-1.3.1-pyhd8ed1ab_0.conda",
            "version": "1.3.1",
        }
    ]


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("", "Invalid receipt JSON"),
        ("[]", "expected a JSON object"),
        (
            '{"_type":"https://in-toto.io/Statement/v1","_type":"x"}',
            "duplicate JSON key",
        ),
    ],
    ids=["empty", "non-object", "duplicate-key"],
)
def test_archive_receipt_load_rejects_invalid_json(
    tmp_path: Path,
    content: str,
    match: str,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(content, encoding="utf-8")

    with pytest.raises(ArchiveError, match=match):
        ArchiveReceipt.load(receipt_path)


def test_archive_receipt_rejects_size_before_json_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text('{"value": 1}', encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(receipts_module, "MAX_RECEIPT_BYTES", 4)
    monkeypatch.setattr(
        receipts_module.json,
        "loads",
        lambda content, **kwargs: calls.append(content),
    )

    with pytest.raises(ArchiveError, match="maximum size"):
        ArchiveReceipt.load(receipt_path)

    assert calls == []


@pytest.mark.parametrize(
    ("content", "boundary", "match"),
    [
        ('{"outer":{"inner":{}}}', "depth", "nesting depth"),
        ('{"first":1,"second":2}', "collection", "collection"),
    ],
    ids=["depth", "collection"],
)
def test_archive_receipt_load_enforces_shape_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    boundary: str,
    match: str,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(content, encoding="utf-8")
    if boundary == "depth":
        monkeypatch.setattr(receipts_module, "MAX_RECEIPT_DEPTH", 1)
    else:
        monkeypatch.setattr(receipts_module, "MAX_RECEIPT_COLLECTION_ITEMS", 1)

    with pytest.raises(ArchiveError, match=match):
        ArchiveReceipt.load(receipt_path)


def test_archive_receipt_load_wraps_decoder_recursion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}", encoding="utf-8")

    def fail_decode(*args, **kwargs):
        raise RecursionError("nested input")

    monkeypatch.setattr(receipts_module.json, "loads", fail_decode)

    with pytest.raises(ArchiveError, match="Invalid receipt"):
        ArchiveReceipt.load(receipt_path)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda statement: statement.__setitem__("_type", "wrong"),
            "statement type",
        ),
        (
            lambda statement: statement.__setitem__("predicateType", "wrong"),
            "predicate type",
        ),
        (
            lambda statement: statement["predicate"]["workspace"].__setitem__(
                "lockfile", "../conda.lock"
            ),
            "relative archive path",
        ),
        (
            lambda statement: statement["subject"].append(statement["subject"][0]),
            "duplicate subject",
        ),
        (
            lambda statement: statement["predicate"]["environments"].append(
                statement["predicate"]["environments"][0]
            ),
            "Duplicate environment",
        ),
        (
            lambda statement: statement["predicate"]["environments"][0][
                "packages"
            ].append(statement["predicate"]["environments"][0]["packages"][0]),
            "Duplicate package",
        ),
    ],
    ids=[
        "statement-type",
        "predicate-type",
        "unsafe-lockfile-path",
        "duplicate-subject",
        "duplicate-environment",
        "duplicate-package",
    ],
)
def test_archive_receipt_validate_rejects_ambiguous_or_unsafe_records(
    receipt_workspace: Path,
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    match: str,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    statement = copied_statement(build_receipt(receipt_workspace, archive_path))
    mutate(statement)

    with pytest.raises(ArchiveError, match=match):
        ArchiveReceipt(statement).validate()


def test_archive_receipt_detects_tampered_archive(
    receipt_workspace: Path,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    receipt = build_receipt(receipt_workspace, archive_path)

    archive_path.write_bytes(archive_path.read_bytes() + b"tamper")

    with pytest.raises(ArchiveError, match="Hash mismatch"):
        receipt.verify_archive(archive_path)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda target: (target / "conda.lock").write_text(
                "version: 1\n",
                encoding="utf-8",
            ),
            "Hash mismatch",
        ),
        (
            lambda target: (target / "conda.lock").unlink(),
            "subject file cannot be read",
        ),
    ],
    ids=["tampered", "missing"],
)
def test_archive_receipt_detects_invalid_extracted_lockfile(
    receipt_workspace: Path,
    tmp_path: Path,
    mutate: Callable[[Path], object],
    match: str,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    receipt = build_receipt(receipt_workspace, archive_path)
    target = tmp_path / "extracted"
    extract_archive(archive_path, target)
    mutate(target)

    with pytest.raises(ArchiveError, match=match):
        receipt.verify_extracted(target)


@pytest.mark.parametrize(
    ("subject_name", "replacement"),
    [
        ("conda.toml", b"[workspace]\nname = 'attacker'\n"),
        (
            "conda.lock",
            b"version: 1\nenvironments:\n  attacker: {}\npackages: []\n",
        ),
    ],
    ids=["manifest", "lockfile"],
)
def test_archive_receipt_returns_exact_verified_workspace_bytes(
    receipt_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject_name: str,
    replacement: bytes,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    receipt = build_receipt(receipt_workspace, archive_path)
    target = tmp_path / "extracted"
    extract_archive(archive_path, target)
    manifest_path = target / "conda.toml"
    lockfile_path = target / "conda.lock"
    manifest_bytes = manifest_path.read_bytes()
    lockfile_bytes = lockfile_path.read_bytes()
    subject_path = target / subject_name
    original_verify = ArchiveReceipt.verify_subject_digest

    def replace_subject_after_digest(
        self: ArchiveReceipt,
        name: str,
        actual: str,
    ) -> None:
        original_verify(self, name, actual)
        if name == subject_name:
            subject_path.write_bytes(replacement)

    monkeypatch.setattr(
        ArchiveReceipt,
        "verify_subject_digest",
        replace_subject_after_digest,
    )

    verified = receipt.verify_extracted(target)

    assert verified == VerifiedArchiveWorkspace(
        manifest_name="conda.toml",
        manifest_bytes=manifest_bytes,
        lockfile_name="conda.lock",
        lockfile_bytes=lockfile_bytes,
    )
    assert subject_path.read_bytes() == replacement


def test_archive_receipt_build_binds_one_lockfile_generation(
    receipt_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    lockfile_path = receipt_workspace / "conda.lock"
    captured = lockfile_path.read_bytes()
    replacement = b"version: 1\nenvironments:\n  attacker: {}\npackages: []\n"
    original_read = receipts_module.read_regular_file_bytes
    lockfile_reads = 0

    def replace_lockfile_after_capture(
        path: Path,
        *,
        maximum_bytes: int,
        label: str,
        directory_descriptor: int | None = None,
    ) -> bytes:
        nonlocal lockfile_reads
        content = original_read(
            path,
            maximum_bytes=maximum_bytes,
            label=label,
            directory_descriptor=directory_descriptor,
        )
        if path == lockfile_path:
            lockfile_reads += 1
            lockfile_path.write_bytes(replacement)
        return content

    monkeypatch.setattr(
        receipts_module,
        "read_regular_file_bytes",
        replace_lockfile_after_capture,
    )

    receipt = build_receipt(receipt_workspace, archive_path)

    assert lockfile_reads == 1
    assert receipt.subject_digests["conda.lock"] == hashlib.sha256(captured).hexdigest()
    assert set(receipt.inventory.environment_names()) == {"default"}
    assert lockfile_path.read_bytes() == replacement


def test_archive_receipt_subject_errors_redact_token_paths(
    receipt_workspace: Path,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    statement = copied_statement(build_receipt(receipt_workspace, archive_path))
    token_path = "t/SENSITIVE-RECEIPT-TOKEN/conda.lock"
    statement["predicate"]["workspace"]["lockfile"] = token_path
    statement["subject"][2]["name"] = token_path
    target = tmp_path / "extracted"
    extract_archive(archive_path, target)

    with pytest.raises(ArchiveError) as exc_info:
        ArchiveReceipt(statement).verify_extracted(target)

    assert "SENSITIVE-RECEIPT-TOKEN" not in str(exc_info.value)
    assert "<redacted-path>" in str(exc_info.value)


@pytest.mark.parametrize(
    ("packages", "match"),
    [
        ([], "Unexpected package record"),
        (
            [
                {
                    "url": "https://example.com/missing.conda",
                    "sha256": "0" * 64,
                }
            ],
            "Missing package record",
        ),
    ],
    ids=["unexpected-actual", "missing-actual"],
)
def test_archive_receipt_validates_package_inventory(
    receipt_workspace: Path,
    tmp_path: Path,
    packages: list[dict[str, object]],
    match: str,
) -> None:
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(receipt_workspace, archive_path, ArchiveConfig())
    receipt = build_receipt(receipt_workspace, archive_path)
    statement = copied_statement(receipt)
    statement["predicate"]["environments"][0]["packages"] = packages
    target = tmp_path / "extracted"
    extract_archive(archive_path, target)

    with pytest.raises(ArchiveError, match=match):
        ArchiveReceipt(statement).verify_extracted(target)


def test_archive_receipt_require_sha256_rejects_md5_only_record(
    tmp_path: Path,
) -> None:
    tmp_path.joinpath("conda.toml").write_text("[workspace]\nname = 'test'\n")
    write_lockfile(tmp_path, sha256=False)
    archive_path = tmp_path / "workspace.tar.gz"
    create_archive(tmp_path, archive_path, ArchiveConfig())
    receipt = build_receipt(tmp_path, archive_path)
    target = tmp_path / "extracted"
    extract_archive(archive_path, target)

    receipt.verify_extracted(target)
    with pytest.raises(ArchiveError, match="lacks sha256"):
        receipt.verify_extracted(target, require_sha256=True)


def test_receipt_inventory_compare_rejects_duplicate_package_identity() -> None:
    env = {
        "name": "default",
        "packages": [
            {"url": "https://example.com/pkg.conda"},
            {"url": "https://example.com/pkg.conda"},
        ],
    }

    with pytest.raises(ArchiveError, match="Duplicate package record"):
        ReceiptInventory([env]).compare(ReceiptInventory([env]))


@pytest.mark.parametrize(
    "mode",
    ["missing", "unexpected", "mismatch", "sha256", "duplicate"],
    ids=["missing", "unexpected", "mismatch", "sha256", "duplicate"],
)
def test_receipt_inventory_errors_redact_package_identity(mode: str) -> None:
    identity = (
        "https://user:password@packages.test/t/TOKENVALUE/private/pkg.conda"
        "?key=query-secret#fragment-secret"
    )
    expected_packages: list[dict[str, object]] = [{"url": identity}]
    actual_packages: list[dict[str, object]] = [{"url": identity}]
    require_sha256 = False

    if mode == "missing":
        actual_packages = []
    elif mode == "unexpected":
        expected_packages = []
    elif mode == "mismatch":
        actual_packages[0]["name"] = "different"
    elif mode == "sha256":
        require_sha256 = True
    else:
        expected_packages.append({"url": identity})

    expected = ReceiptInventory([{"name": "default", "packages": expected_packages}])
    actual = ReceiptInventory([{"name": "default", "packages": actual_packages}])

    with pytest.raises(ArchiveError) as exc_info:
        expected.compare(actual, require_sha256=require_sha256)

    message = str(exc_info.value)
    assert "https://packages.test/private/pkg.conda" in message
    for secret in (
        "user",
        "password",
        "TOKENVALUE",
        "query-secret",
        "fragment-secret",
    ):
        assert secret not in message


def test_receipt_package_record_identity_fallbacks() -> None:
    assert ReceiptPackageRecord({"fn": "pkg-1.0-h0.conda"}).identity == (
        "pkg-1.0-h0.conda"
    )
    assert (
        ReceiptPackageRecord(
            {
                "name": "pkg",
                "version": "1.0",
                "build": "h0",
                "channel": "https://conda.anaconda.org/conda-forge/",
            }
        ).identity
        == "pkg|1.0|h0|https://conda.anaconda.org/conda-forge/"
    )


def test_receipt_package_record_redacts_encoded_channel_token() -> None:
    package = ReceiptPackageRecord.from_record(
        {
            "url": "https://packages.example.test/t%252FSENSITIVE-VALUE/linux-64/pkg-1-0.conda",
            "channel": "HTTPS://user:SENSITIVE-VALUE@packages.example.test/private",
        }
    )

    assert package.data["url"] == (
        "https://packages.example.test/linux-64/pkg-1-0.conda"
    )
    assert package.data["channel"] == "HTTPS://packages.example.test/private"


def test_receipt_package_record_redacts_relative_tokens() -> None:
    package = ReceiptPackageRecord.from_record(
        {
            "url": "t/INFO-LEAK/private/linux-64/pkg-1-0.conda",
            "channel": "t/CHANNEL-LEAK/private",
        },
        platform="linux-64",
    )

    serialized = json.dumps(package.data)
    assert "INFO-LEAK" not in serialized
    assert "CHANNEL-LEAK" not in serialized
    assert package.data["url"] == "<redacted-url-value>"
    assert package.data["channel"] == "<redacted-url-value>"
