"""Tests for conda_workspaces.publication."""

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import conda_workspaces.paths as paths_mod
import conda_workspaces.publication as publication_mod
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.exceptions import CondaWorkspacesError, FileRecoveryError
from conda_workspaces.manifests import detect_and_parse
from conda_workspaces.paths import supports_anchored_directory_operations
from conda_workspaces.publication import WorkspacePublication

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any


@pytest.fixture
def publication_manifest(tmp_path: Path) -> Path:
    """Create a minimal manifest accepted by every publication test."""
    path = tmp_path / "conda.toml"
    path.write_text(
        """\
[workspace]
name = "publication-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.12"
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def make_publication() -> Callable[..., WorkspacePublication]:
    """Return a factory bound to the manifest's parsed generation."""

    def factory(
        manifest: Path,
        *,
        updated_text: str | None = None,
    ) -> WorkspacePublication:
        _, config = detect_and_parse(manifest)
        original_text = config._manifest_text
        assert original_text is not None
        return WorkspacePublication(
            WorkspaceContext(config),
            manifest,
            original_text,
            original_text if updated_text is None else updated_text,
            "test",
        )

    return factory


def test_guard_tracks_lifecycle_and_rejects_reentry(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    publication = make_publication(publication_manifest)

    with pytest.raises(RuntimeError, match="already held"):
        with publication.guard():
            assert publication._guarded is True
            with publication.guard():
                pass

    assert publication._guarded is False
    assert publication.lock_path.is_file()


def test_from_current_manifest_keeps_exact_parsed_generation(
    publication_manifest: Path,
) -> None:
    original_text = publication_manifest.read_bytes().decode("utf-8")
    _, config = detect_and_parse(publication_manifest)
    publication_manifest.write_bytes(
        original_text.replace("publication-test", "replacement").encode("utf-8")
    )

    publication = WorkspacePublication.from_current_manifest(
        WorkspaceContext(config),
        "install",
    )

    assert publication.original_text == original_text
    with pytest.raises(CondaWorkspacesError, match="manifest changed"):
        with publication.guard():
            pass


def test_publish_lockfile_writes_manifest_before_lock(
    publication_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_publication: Callable[..., WorkspacePublication],
    replace_publication_writer,
) -> None:
    original_text = publication_manifest.read_text(encoding="utf-8")
    updated_text = original_text.replace('python = ">=3.12"', 'python = ">=3.13"')
    publication = make_publication(
        publication_manifest,
        updated_text=updated_text,
    )
    events: list[str] = []

    def record_publication(path, content, write) -> None:
        events.append("lockfile" if path.name == "conda.lock" else "manifest")
        write(content)

    replace_publication_writer(record_publication)

    publication.publish_lockfile("version: 1\nenvironments: {}\npackages: []\n")

    assert events == ["manifest", "lockfile"]
    assert publication_manifest.read_bytes().decode("utf-8") == updated_text
    assert publication.started is True


def test_publish_manifest_accepts_its_generation_and_rejects_later_changes(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    original_text = publication_manifest.read_bytes().decode("utf-8")
    updated_text = original_text.replace('python = ">=3.12"', 'python = ">=3.13"')
    publication = make_publication(
        publication_manifest,
        updated_text=updated_text,
    )

    with publication.guard():
        publication.publish_manifest()
        publication.validate_manifest_generation()
        assert publication_manifest.read_bytes().decode("utf-8") == updated_text

        concurrent_text = updated_text.replace('python = ">=3.13"', 'python = ">=3.14"')
        publication_manifest.write_bytes(concurrent_text.encode("utf-8"))
        with pytest.raises(CondaWorkspacesError, match="manifest changed"):
            publication.validate_manifest_generation()

    assert publication.original_text == original_text
    assert publication_manifest.read_bytes().decode("utf-8") == concurrent_text


def test_publish_lockfile_keeps_manifest_when_lock_write_fails(
    publication_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_publication: Callable[..., WorkspacePublication],
    replace_publication_writer,
) -> None:
    original_text = publication_manifest.read_text(encoding="utf-8")
    updated_text = original_text.replace('python = ">=3.12"', 'python = ">=3.13"')
    publication = make_publication(
        publication_manifest,
        updated_text=updated_text,
    )
    lock_path = publication_manifest.with_name("conda.lock")
    previous_lock = "version: 1\nenvironments: {}\npackages: []\n"
    lock_path.write_text(previous_lock, encoding="utf-8")

    def fail_lockfile(path, content, write) -> None:
        if path.name == "conda.lock":
            raise RuntimeError("publication failed")
        write(content)

    replace_publication_writer(fail_lockfile)

    with pytest.raises(RuntimeError, match="publication failed"):
        publication.publish_lockfile("replacement")

    assert publication_manifest.read_bytes().decode("utf-8") == updated_text
    assert lock_path.read_text(encoding="utf-8") == previous_lock
    assert publication.started is True


def test_publish_lockfile_does_not_overwrite_unknown_failure_generation(
    publication_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_publication: Callable[..., WorkspacePublication],
    replace_publication_writer,
) -> None:
    original_text = publication_manifest.read_text(encoding="utf-8")
    updated_text = original_text.replace('python = ">=3.12"', 'python = ">=3.13"')
    publication = make_publication(
        publication_manifest,
        updated_text=updated_text,
    )
    lock_path = publication_manifest.with_name("conda.lock")
    lock_path.write_text("previous", encoding="utf-8")

    def publish_unknown_generation(path, content, write) -> None:
        if path.name == "conda.lock":
            write("unknown generation")
            raise RuntimeError("publication state is uncertain")
        write(content)

    replace_publication_writer(publish_unknown_generation)

    with pytest.raises(RuntimeError, match="publication state is uncertain"):
        publication.publish_lockfile("replacement")

    assert publication_manifest.read_bytes().decode("utf-8") == updated_text
    assert lock_path.read_text(encoding="utf-8") == "unknown generation"
    assert publication.started is True


def test_publish_lockfile_rejects_changed_read_generation(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    original_text = publication_manifest.read_text(encoding="utf-8")
    publication = make_publication(
        publication_manifest,
        updated_text=original_text.replace('python = ">=3.12"', 'python = ">=3.13"'),
    )
    lock_path = publication_manifest.with_name("conda.lock")
    baseline = b"version: 1\nenvironments: {}\npackages: []\n"
    lock_path.write_bytes(baseline)
    replacement = publication_manifest.with_name("replacement.lock")
    replacement.write_text("concurrent generation", encoding="utf-8")

    with publication.guard():
        assert publication.read_lockfile_bytes() == baseline
        replacement.replace(lock_path)
        with pytest.raises(CondaWorkspacesError, match="lockfile changed"):
            publication.publish_lockfile("generated from stale input")

    assert lock_path.read_text(encoding="utf-8") == "concurrent generation"
    assert publication_manifest.read_text(encoding="utf-8") == original_text


def test_publish_lockfile_refreshes_exact_snapshot(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    lock_path.write_text("old lock", encoding="utf-8")
    rendered = "version: 1\nenvironments: {}\npackages: []\n"

    with publication.guard():
        publication.publish_lockfile(rendered)
        snapshot = publication.snapshot("conda-toml")

    assert snapshot.manifest_path == publication_manifest
    assert snapshot.manifest_name == "conda.toml"
    assert snapshot.manifest_bytes == publication_manifest.read_bytes()
    assert snapshot.manifest_format == "conda-toml"
    assert snapshot.lockfile_path == lock_path
    assert snapshot.lockfile_name == "conda.lock"
    assert snapshot.lockfile_bytes == rendered.encode("utf-8")


@pytest.mark.parametrize(
    "previous_content",
    [None, b"\xffprevious lockfile\n"],
    ids=["missing", "existing-exact-bytes"],
)
@pytest.mark.parametrize(
    "restore_failure",
    [False, True],
    ids=["restore", "retained-recovery"],
)
def test_reversible_lockfile_publication_restores_previous_state(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
    monkeypatch: pytest.MonkeyPatch,
    previous_content: bytes | None,
    restore_failure: bool,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    if previous_content is not None:
        lock_path.write_bytes(previous_content)
    rendered = "version: 1\nenvironments: {}\npackages: []\n"
    recovery = lock_path.with_name(".conda.lock.recovery.rollback")
    claimant = b"claimant lockfile\n"
    if restore_failure and previous_content is None:

        def fail_removal(path: Path, *args, **kwargs) -> bool:
            path.rename(recovery)
            path.write_bytes(claimant)
            raise FileRecoveryError(path, recovery, "Lockfile removal failed.")

        monkeypatch.setattr(publication_mod, "remove_file_generation", fail_removal)
    elif restore_failure:

        @contextmanager
        def fail_restore(path: Path, **kwargs: Any) -> Iterator[BytesIO]:
            yield BytesIO()
            path.rename(recovery)
            path.write_bytes(claimant)
            raise FileRecoveryError(path, recovery, "Lockfile restore failed.")

        @contextmanager
        def fail_restore_at(
            directory_descriptor: int,
            name: str,
            *,
            display_path: Path,
            **kwargs: Any,
        ) -> Iterator[BytesIO]:
            yield BytesIO()
            display_path.rename(recovery)
            display_path.write_bytes(claimant)
            raise FileRecoveryError(
                display_path,
                recovery,
                "Lockfile restore failed.",
            )

        monkeypatch.setattr(publication_mod, "atomic_binary_writer", fail_restore)
        monkeypatch.setattr(publication_mod, "atomic_binary_writer_at", fail_restore_at)

    with publication.guard():
        expected_error = FileRecoveryError if restore_failure else RuntimeError
        with pytest.raises(expected_error) as exc_info:
            with publication.reversible_lockfile_publication(rendered) as rollback:
                assert rollback.previous_content == previous_content
                assert (rollback.previous_generation is None) is (
                    previous_content is None
                )
                assert rollback.published_content == rendered.encode("utf-8")
                assert lock_path.read_bytes() == rollback.published_content
                raise RuntimeError("sidecar failed")

    if restore_failure:
        assert isinstance(exc_info.value, FileRecoveryError)
        assert exc_info.value.recovery_path == recovery
        assert lock_path.read_bytes() == claimant
        assert recovery.read_bytes() == rendered.encode("utf-8")
    elif previous_content is None:
        assert not lock_path.exists()
    else:
        assert lock_path.read_bytes() == previous_content


def test_reversible_lockfile_publication_aggregates_recovery_entries(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    operation_recovery = publication_manifest.with_name("operation.rollback")
    rollback_recovery = publication_manifest.with_name("restore.rollback")
    operation_recovery.write_bytes(b"operation recovery")
    rollback_recovery.write_bytes(b"rollback recovery")

    def fail_restore(rollback: publication_mod.LockfileRollback) -> bool:
        raise FileRecoveryError(
            lock_path,
            rollback_recovery,
            f"Rollback failed for {rollback.published_generation}.",
        )

    monkeypatch.setattr(publication, "restore_lockfile", fail_restore)

    with publication.guard():
        with pytest.raises(FileRecoveryError) as exc_info:
            with publication.reversible_lockfile_publication(
                "version: 1\nenvironments: {}\npackages: []\n"
            ):
                raise FileRecoveryError(
                    lock_path,
                    operation_recovery,
                    "Later operation failed.",
                )

    assert exc_info.value.recovery_paths == (
        operation_recovery,
        rollback_recovery,
    )
    assert "Later operation failed" in str(exc_info.value)
    assert "Rollback failed" in str(exc_info.value)


@pytest.mark.parametrize(
    "previous_content",
    [None, b"previous lockfile\n"],
    ids=["missing", "existing"],
)
def test_reversible_lockfile_publication_restores_post_write_failure(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
    monkeypatch: pytest.MonkeyPatch,
    previous_content: bytes | None,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    if previous_content is not None:
        lock_path.write_bytes(previous_content)
    rendered = "version: 1\nenvironments: {}\npackages: []\n"
    original_read = publication_mod.read_regular_file_bytes_with_generation
    failed = False

    def fail_first_post_write_capture(*args, **kwargs):
        nonlocal failed
        if publication._lockfile_publication_generation is not None and not failed:
            failed = True
            raise ValueError("post-write capture failed")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(
        publication_mod,
        "read_regular_file_bytes_with_generation",
        fail_first_post_write_capture,
    )

    with publication.guard():
        with pytest.raises(CondaWorkspacesError, match="during publication"):
            with publication.reversible_lockfile_publication(rendered):
                pass

    assert failed is True
    if previous_content is None:
        assert not lock_path.exists()
    else:
        assert lock_path.read_bytes() == previous_content


@pytest.mark.parametrize(
    "concurrent_content",
    [
        b"version: 1\nenvironments: {}\npackages: []\n",
        b"concurrent replacement\n",
    ],
    ids=["same-bytes", "different-bytes"],
)
def test_reversible_lockfile_publication_preserves_remove_race(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
    monkeypatch: pytest.MonkeyPatch,
    concurrent_content: bytes,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    rendered = "version: 1\nenvironments: {}\npackages: []\n"
    original_rename = paths_mod.rename_noreplace
    raced = False

    def replace_before_quarantine(*args, **kwargs) -> None:
        nonlocal raced
        source = Path(args[0])
        if source.name == lock_path.name and not raced:
            replacement = lock_path.with_name("concurrent.lock")
            replacement.write_bytes(concurrent_content)
            replacement.replace(lock_path)
            raced = True
        original_rename(*args, **kwargs)

    monkeypatch.setattr(paths_mod, "rename_noreplace", replace_before_quarantine)

    with publication.guard():
        with pytest.raises(RuntimeError, match="sidecar failed"):
            with publication.reversible_lockfile_publication(rendered):
                raise RuntimeError("sidecar failed")

    assert raced is True
    assert lock_path.read_bytes() == concurrent_content


def test_reversible_lockfile_publication_keeps_successful_generation(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    lock_path.write_bytes(b"previous lockfile")
    rendered = "version: 1\nenvironments: {}\npackages: []\n"

    with publication.guard():
        with publication.reversible_lockfile_publication(rendered) as rollback:
            assert rollback.published_generation != rollback.previous_generation

    assert lock_path.read_bytes() == rendered.encode("utf-8")


@pytest.mark.parametrize(
    "previous_content",
    [None, b"previous lockfile"],
    ids=["missing", "existing"],
)
@pytest.mark.parametrize(
    "mutation",
    ["replace-with-same-bytes", "rewrite-with-different-bytes"],
)
def test_reversible_lockfile_publication_preserves_concurrent_generation(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
    previous_content: bytes | None,
    mutation: str,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    if previous_content is not None:
        lock_path.write_bytes(previous_content)
    rendered = "version: 1\nenvironments: {}\npackages: []\n"
    rendered_bytes = rendered.encode("utf-8")
    concurrent_content = rendered_bytes

    with publication.guard():
        with pytest.raises(RuntimeError, match="sidecar failed"):
            with publication.reversible_lockfile_publication(rendered):
                if mutation == "replace-with-same-bytes":
                    replacement = lock_path.with_name("replacement.lock")
                    replacement.write_bytes(rendered_bytes)
                    replacement.replace(lock_path)
                else:
                    concurrent_content = b"concurrent lockfile"
                    lock_path.write_bytes(concurrent_content)
                raise RuntimeError("sidecar failed")

    assert lock_path.read_bytes() == concurrent_content


@pytest.mark.parametrize("target", ["manifest", "lockfile"])
def test_validate_snapshot_rejects_generation_change(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
    target: str,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    lock_path.write_text("stable lock", encoding="utf-8")

    with publication.guard():
        snapshot = publication.snapshot("conda-toml")
        path = publication_manifest if target == "manifest" else lock_path
        replacement = path.with_name(f"replacement-{path.name}")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        with pytest.raises(CondaWorkspacesError, match=f"{target} changed"):
            publication.validate_snapshot(snapshot)


@pytest.mark.parametrize(
    ("mutation", "preserve_generation"),
    [
        ("replace", False),
        ("rewrite", False),
        ("rewrite", True),
    ],
    ids=["replace", "rewrite", "same-generation-rewrite"],
)
def test_publish_lockfile_rejects_guarded_generation_change_without_read(
    publication_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_publication: Callable[..., WorkspacePublication],
    mutation: str,
    preserve_generation: bool,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    original_content = b"AAAA"
    concurrent_content = b"BBBB"
    lock_path.write_bytes(original_content)
    original_read = publication_mod.read_regular_file_bytes_with_generation

    with publication.guard():
        if mutation == "replace":
            replacement = lock_path.with_name("replacement.lock")
            replacement.write_bytes(concurrent_content)
            replacement.replace(lock_path)
        else:
            lock_path.write_bytes(concurrent_content)
        if preserve_generation:
            captured_generation = publication._lockfile_generation
            assert captured_generation is not None

            def read_with_captured_generation(*args: Any, **kwargs: Any):
                content, generation = original_read(*args, **kwargs)
                if Path(args[0]) == lock_path:
                    generation = captured_generation
                return content, generation

            monkeypatch.setattr(
                publication_mod,
                "read_regular_file_bytes_with_generation",
                read_with_captured_generation,
            )
        with pytest.raises(CondaWorkspacesError, match="lockfile changed"):
            publication.publish_lockfile("generated without reading the lockfile")

    assert len(concurrent_content) == len(original_content)
    assert lock_path.read_bytes() == concurrent_content


@pytest.mark.parametrize("mutation", ["replace", "rewrite"])
def test_publish_lockfile_rejects_changed_generation_at_writer(
    publication_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_publication: Callable[..., WorkspacePublication],
    mutation: str,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    baseline = b"version: 1\nenvironments: {}\npackages: []\n"
    lock_path.write_bytes(baseline)
    concurrent_content = "concurrent lock generation"
    original_atomic_write_text = publication_mod.atomic_write_text
    original_atomic_write_text_at = publication_mod.atomic_write_text_at
    mutated = False

    def mutate_lockfile() -> None:
        nonlocal mutated
        if mutated:
            return
        if mutation == "replace":
            replacement = lock_path.with_name("replacement.lock")
            replacement.write_text(concurrent_content, encoding="utf-8")
            replacement.replace(lock_path)
        else:
            lock_path.write_text(concurrent_content, encoding="utf-8")
        mutated = True

    def mutate_before_write(path: Path, content: str, **kwargs) -> None:
        mutate_lockfile()
        original_atomic_write_text(path, content, **kwargs)

    def mutate_before_write_at(
        directory_descriptor: int,
        name: str,
        content: str,
        **kwargs,
    ) -> None:
        mutate_lockfile()
        original_atomic_write_text_at(
            directory_descriptor,
            name,
            content,
            **kwargs,
        )

    with publication.guard():
        assert publication.read_lockfile_bytes() == baseline
        monkeypatch.setattr(publication_mod, "atomic_write_text", mutate_before_write)
        monkeypatch.setattr(
            publication_mod,
            "atomic_write_text_at",
            mutate_before_write_at,
        )
        with pytest.raises(CondaWorkspacesError, match="changed before publication"):
            publication.publish_lockfile("generated from stale input")

    assert mutated is True
    assert lock_path.read_text(encoding="utf-8") == concurrent_content


@pytest.mark.parametrize("mutation", ["replace", "rewrite"])
def test_publish_manifest_rejects_changed_validated_generation(
    publication_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_publication: Callable[..., WorkspacePublication],
    mutation: str,
) -> None:
    original_text = publication_manifest.read_text(encoding="utf-8")
    publication = make_publication(
        publication_manifest,
        updated_text=original_text.replace('python = ">=3.12"', 'python = ">=3.13"'),
    )
    concurrent_text = original_text.replace(
        'python = ">=3.12"',
        'python = ">=3.140"',
    )
    original_atomic_write_text = publication_mod.atomic_write_text
    original_atomic_write_text_at = publication_mod.atomic_write_text_at
    mutated = False

    def mutate_manifest() -> None:
        nonlocal mutated
        if mutated:
            return
        if mutation == "replace":
            replacement = publication_manifest.with_name("replacement.toml")
            replacement.write_text(concurrent_text, encoding="utf-8")
            replacement.replace(publication_manifest)
        else:
            publication_manifest.write_text(concurrent_text, encoding="utf-8")
        mutated = True

    def mutate_before_write(path: Path, content: str, **kwargs) -> None:
        mutate_manifest()
        original_atomic_write_text(path, content, **kwargs)

    def mutate_before_write_at(
        directory_descriptor: int,
        name: str,
        content: str,
        **kwargs,
    ) -> None:
        mutate_manifest()
        original_atomic_write_text_at(
            directory_descriptor,
            name,
            content,
            **kwargs,
        )

    monkeypatch.setattr(publication_mod, "atomic_write_text", mutate_before_write)
    monkeypatch.setattr(publication_mod, "atomic_write_text_at", mutate_before_write_at)

    with publication.guard():
        with pytest.raises(CondaWorkspacesError, match="changed before publication"):
            publication.publish_manifest()

    assert mutated is True
    assert publication_manifest.read_text(encoding="utf-8") == concurrent_text


def test_guard_does_not_follow_replaced_state_directory(
    publication_manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    if not supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")

    publication = make_publication(publication_manifest)
    state_dir = publication_manifest.parent / ".conda"
    state_dir.mkdir()
    displaced_state = publication_manifest.parent / "displaced-state"
    outside = publication_manifest.parent / "outside"
    outside.mkdir()
    real_open = publication_mod.os.open
    replaced = False

    def replace_state(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if path == "workspace.lock" and dir_fd is not None and not replaced:
            state_dir.rename(displaced_state)
            state_dir.symlink_to(outside, target_is_directory=True)
            replaced = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(publication_mod.os, "open", replace_state)

    with pytest.raises(CondaWorkspacesError, match="publication lock changed"):
        with publication.guard():
            pass

    assert replaced is True
    assert not (outside / "workspace.lock").exists()


def test_guard_rejects_hardlinked_lock_without_modifying_victim(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    publication = make_publication(publication_manifest)
    publication.lock_path.parent.mkdir()
    victim = publication_manifest.parent / "victim.txt"
    victim.write_bytes(b"keep")
    publication.lock_path.hardlink_to(victim)

    with pytest.raises(CondaWorkspacesError, match="publication lock"):
        with publication.guard():
            pass

    assert victim.read_bytes() == b"keep"
    assert publication.lock_path.read_bytes() == b"keep"


def test_guard_rejects_workspace_root_replacement_before_publication(
    tmp_path: Path,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    if not supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")
    root = tmp_path / "workspace"
    root.mkdir()
    manifest = root / "conda.toml"
    manifest_text = (
        "[workspace]\nname = 'original'\nchannels = ['conda-forge']\n"
        "platforms = ['linux-64']\n"
    )
    manifest.write_text(manifest_text, encoding="utf-8")
    original = manifest.read_text(encoding="utf-8")
    publication = make_publication(
        manifest,
        updated_text=original.replace("original", "updated"),
    )
    displaced = tmp_path / "displaced-workspace"

    with pytest.raises(CondaWorkspacesError, match="publication lock changed"):
        with publication.guard():
            root.rename(displaced)
            root.mkdir()
            (root / "conda.toml").write_text(original, encoding="utf-8")
            publication.publish_lockfile("version: 1\nenvironments: {}\npackages: []\n")

    assert (root / "conda.toml").read_text(encoding="utf-8") == original
    assert not (root / "conda.lock").exists()
    assert (displaced / "conda.toml").read_text(encoding="utf-8") == original
    assert not (displaced / "conda.lock").exists()


def test_guarded_lockfile_read_stays_anchored_during_root_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_publication: Callable[..., WorkspacePublication],
) -> None:
    if not supports_anchored_directory_operations():
        pytest.skip("descriptor-relative directory operations are unavailable")

    root = tmp_path / "workspace"
    root.mkdir()
    manifest = root / "conda.toml"
    manifest_text = (
        "[workspace]\nname = 'original'\nchannels = ['conda-forge']\n"
        "platforms = ['linux-64']\n"
    )
    manifest.write_text(manifest_text, encoding="utf-8")
    expected = b"version: 1\nenvironments: {}\npackages: []\n"
    (root / "conda.lock").write_bytes(expected)
    publication = make_publication(manifest)
    displaced = tmp_path / "displaced-workspace"
    original_read = publication_mod.read_regular_file_bytes_with_generation
    swapped = False

    def swap_while_reading(*args, **kwargs):
        nonlocal swapped
        root.rename(displaced)
        root.mkdir()
        (root / "conda.toml").write_text(manifest_text, encoding="utf-8")
        (root / "conda.lock").write_bytes(b"attacker")
        try:
            result = original_read(*args, **kwargs)
        finally:
            (root / "conda.lock").unlink()
            (root / "conda.toml").unlink()
            root.rmdir()
            displaced.rename(root)
        swapped = True
        return result

    monkeypatch.setattr(
        publication_mod,
        "read_regular_file_bytes_with_generation",
        swap_while_reading,
    )

    with publication.guard():
        actual = publication.read_lockfile_bytes()

    assert swapped is True
    assert actual == expected
