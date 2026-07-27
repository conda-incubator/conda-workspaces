"""Tests for conda_workspaces.publication."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import conda_workspaces.publication as publication_mod
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.exceptions import CondaWorkspacesError
from conda_workspaces.manifests import detect_and_parse
from conda_workspaces.paths import supports_anchored_directory_operations
from conda_workspaces.publication import WorkspacePublication

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


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


@pytest.mark.parametrize("mutation", ["replace", "rewrite"])
def test_publish_lockfile_rejects_guarded_generation_change_without_read(
    publication_manifest: Path,
    make_publication: Callable[..., WorkspacePublication],
    mutation: str,
) -> None:
    publication = make_publication(publication_manifest)
    lock_path = publication_manifest.with_name("conda.lock")
    lock_path.write_text("original lock generation", encoding="utf-8")
    concurrent = "concurrent lock generation"

    with publication.guard():
        if mutation == "replace":
            replacement = lock_path.with_name("replacement.lock")
            replacement.write_text(concurrent, encoding="utf-8")
            replacement.replace(lock_path)
        else:
            lock_path.write_text(concurrent, encoding="utf-8")
        with pytest.raises(CondaWorkspacesError, match="lockfile changed"):
            publication.publish_lockfile("generated without reading the lockfile")

    assert lock_path.read_text(encoding="utf-8") == concurrent


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
