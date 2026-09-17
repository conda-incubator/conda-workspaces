# Use workspace archives from Python

Use `conda_workspaces.archive.WorkspaceArchive` when another Python tool
needs archive behavior without shelling out to `conda workspace archive`
or importing CLI handlers.

## Create an archive

Create an archive from the current workspace:

```python
from conda_workspaces.archive import WorkspaceArchive

archive = WorkspaceArchive.create(
    output="dist/my-project.tar.zst",
    receipt=True,
)
```

Pass `workspace=` to archive a different workspace root:

```python
archive = WorkspaceArchive.create(
    workspace="/path/to/workspace",
    output="dist/my-project.tar.zst",
    receipt="dist/my-project.receipt.json",
)
```

Set `lock=True` to refresh `conda.lock` before writing the archive, and
`bundle=True` to include resolved package archives from conda's package
cache:

```python
archive = WorkspaceArchive.create(
    output="dist/my-project-offline.tar.zst",
    lock=True,
    bundle=True,
    receipt=True,
)
```

## Inspect and verify an archive

Inspect archive metadata without extracting files:

```python
archive = WorkspaceArchive("dist/my-project.tar.zst", receipt=True)
info = archive.inspect()

if not info["has_manifest"]:
    raise RuntimeError("not a workspace archive")
```

Verify an archive against its receipt:

```python
receipt = archive.verify()
print(receipt.workspace_paths)
```

Receipt verification, member inspection, and extraction use one private archive
snapshot. Replacing the original path after snapshotting cannot change the
verified bytes. A receipt still does not identify who created the archive or
receipt.

## Extract an archive

Archive inspection and extraction reject more than 100,000 members, member
paths or link targets deeper than 256 components or longer than 4,096 UTF-8
bytes, more than 128 consecutive GNU or PAX extension headers, more than 100,000
PAX records, more than 64 MiB of expanded GNU and PAX metadata, and more than
100 GiB of declared regular-file data including possible link fallbacks. GNU
sparse files, hardlinks, and unsupported tar members are rejected before their
payloads are traversed. Extraction requires a supported Python patch release
with `tarfile.data_filter`. Repack sparse or metadata-heavy inputs as ordinary
files, split an archive, or exclude unneeded files when a trusted workspace
exceeds one of these safety limits.

When a receipt is configured, its archive digest is verified before member
inspection. Archive creation also rejects a symbolic-link workspace manifest
before reading its target. It refuses credentials embedded in the manifest or
existing lockfile, then binds the approved manifest, lockfile, and package bytes
to the bytes written into the archive. Remove and rotate embedded credentials,
regenerate `conda.lock`, and configure replacement authentication through Conda
outside the repository. Bundle and receipt lock inputs are read while workspace
publication is locked. An existing archive output is replaced only if it has not
changed. A receipt failure removes a new archive output but keeps a generated
canonical lockfile that was already published successfully. On Windows, an
interrupted replacement can leave the destination absent and the prior output in
a uniquely named `.rollback` file beside it.

Extract into a target path that does not exist yet:

```python
result = archive.extract(target="/tmp/restored", require_sha256=True)

print(result.target)
print(result.verified)
```

When a bundled archive has a verified receipt, extraction can prime the
local conda package cache from packages stored inside the archive. Without
a receipt, bundled packages are left in the extracted workspace and cache
priming is skipped.

Pass `prime_cache=False` to disable cache priming:

```python
archive.extract(target="/tmp/restored", prime_cache=False)
```

## Install from an archive

Install all archived environments after extraction:

```python
archive.install(target="/tmp/restored")
```

Install one environment to an explicit runtime prefix:

```python
archive.install(
    target="/tmp/restored",
    environment="runtime",
    prefix="/opt/runtime",
)
```

Stage files under a filesystem root while preserving the requested runtime
prefix:

```python
result = archive.install(
    target="/tmp/restored",
    environment="runtime",
    prefix="/opt/runtime",
    dest="/tmp/rootfs",
)

if result.prefix_reference_matches:
    print("Some files still reference the staging prefix")
```

`prefix_reference_matches` reports files that still contain the physical
staging prefix after installation. It is a warning signal for relocation
workflows, not an automatic prefix-rewrite feature.

## Customize installation

Pass `install_handler=` when an integration wants conda-workspaces to
extract and verify an archive, but wants to control environment
installation:

```python
from pathlib import Path

from conda_workspaces.receipts import VerifiedArchiveWorkspace


def install_handler(
    workspace: Path,
    environment: str | None,
    prefix: Path | None,
    target_prefix_override: str | None,
    *,
    verified_workspace: VerifiedArchiveWorkspace | None = None,
) -> int:
    if verified_workspace is not None:
        print(
            verified_workspace.manifest_name,
            verified_workspace.lockfile_name,
        )
    print(workspace, environment, prefix, target_prefix_override)
    return 0


archive.install(
    target="/tmp/restored",
    environment="runtime",
    prefix="/opt/runtime",
    dest="/tmp/rootfs",
    install_handler=install_handler,
)
```

The handler receives the extracted workspace path, the selected environment,
the physical install prefix, and the runtime prefix override when staging under
`dest`. When a receipt or signed receipt verifies the archive, the
`verified_workspace` keyword argument contains the exact manifest and lockfile bytes
that passed verification. A custom handler must accept and consume this argument
instead of reopening those files from the extracted workspace.

## API reference

See [](../reference/api/archive.md) for the formal API reference.
