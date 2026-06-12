from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.archive import create_archive, extract_archive
from conda_workspaces.exceptions import ArchiveError
from conda_workspaces.models import ArchiveConfig
from conda_workspaces.receipts import (
    ARCHIVE_RECEIPT_PREDICATE_TYPE,
    IN_TOTO_STATEMENT_TYPE,
    ArchiveReceipt,
    ReceiptInventory,
    ReceiptPackageRecord,
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
