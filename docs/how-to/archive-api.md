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

Receipt verification checks the archive digest before extraction. It
does not identify who created the archive or receipt.

## Extract an archive

Extract into an empty or missing target directory:

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


def install_handler(
    workspace: Path,
    environment: str | None,
    prefix: Path | None,
    target_prefix_override: str | None,
) -> int:
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

The handler receives the extracted workspace path, the selected
environment, the physical install prefix, and the runtime prefix override
when staging under `dest`.

## API reference

See [](../reference/api/archive.md) for the formal API reference.
