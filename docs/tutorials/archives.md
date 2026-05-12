# Archives

This tutorial walks through packaging a workspace into a portable
archive and restoring it on another machine or in CI.

## Prerequisites

- conda (>= 24.7) with the conda-workspaces plugin installed
- A workspace with a `conda.toml` and `conda.lock`

## Create a workspace archive

An archive bundles your manifest, lockfile, and source files into a
single `.tar.gz` (or `.tar.zst`) file. In a git repo, only tracked
files are included.

```bash
conda workspace archive -o my-project.tar.gz
```

The resulting file contains everything needed to reproduce the
workspace elsewhere:

```
my-project.tar.gz
  conda.toml
  conda.lock
  src/
    app.py
    ...
```

If no `-o` is given, the archive is named after the workspace
(`<name>.tar.gz`) and placed in the project root.

## Use zstandard compression

For smaller archives, use a `.tar.zst` extension. conda-workspaces
detects the format from the filename and compresses at level 19:

```bash
conda workspace archive -o my-project.tar.zst
```

## Exclude files

Some files should not be included in archives. Configure permanent
exclusions in your manifest:

```toml
[workspace.archive]
exclude = ["docs/**", "*.log", "data/raw/**"]
```

Or pass one-off exclusions on the command line:

```bash
conda workspace archive --exclude "benchmarks/**" --exclude "*.csv"
```

Both sources are combined. Built-in exclusions (`.git`, `__pycache__`,
`.conda/envs`, `.pixi`) always apply regardless of configuration.

## Extract an archive

On the receiving end, extract the archive with:

```bash
conda workspace unarchive my-project.tar.gz
```

This creates a `my-project/` directory (derived from the archive
filename) containing the full workspace. To choose a different
location:

```bash
conda workspace unarchive my-project.tar.gz --target /path/to/destination
```

After extraction, install the environments from the lockfile:

```bash
cd my-project
conda workspace install --locked
```

## Bundle packages for offline use

When the target machine has no internet access, use `--bundle` to
include all resolved `.conda` packages inside the archive:

```bash
conda workspace archive --bundle -o my-project-offline.tar.gz
```

This adds a `packages/` directory inside the archive. Package hashes
are verified against the lockfile before bundling.

On the receiving end, `conda workspace unarchive` detects the bundled
packages, verifies their hashes, and copies them into the local conda
cache before installation:

```bash
conda workspace unarchive my-project-offline.tar.gz
# packages are primed into the conda cache automatically
cd my-project-offline
conda workspace install --locked
```

Pass `--no-install` to skip cache priming if you only want the files:

```bash
conda workspace unarchive my-project-offline.tar.gz --no-install
```

## Security

Archives are extracted with path traversal protection. Every member is
validated before extraction, and on Python 3.12+ the `filter="data"`
parameter provides additional defense-in-depth.

Unsigned archives produce a warning on extraction:

```
WARNING: Archive is not signed. Cannot verify origin or integrity.
```

Review the manifest and lockfile before installing environments from
an unsigned archive.

## Next steps

- [Configuration](../configuration.md#archive-configuration) for all
  archive settings
- [Features](../features.md#archives) for a feature overview
- [CLI reference](../reference/cli.md) for the full `archive` and
  `unarchive` command-line options
