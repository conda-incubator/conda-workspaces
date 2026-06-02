# Archives

![archive demo](../../demos/archives.gif)

This tutorial walks through packaging a workspace into a portable
archive and restoring it on another machine or in CI.

## Prerequisites

- conda (>= 26.3) with the conda-workspaces plugin installed
- A workspace with a manifest (`conda.toml`, `pixi.toml`, or `pyproject.toml`) and `conda.lock`

## Create a workspace archive

An archive bundles your manifest, lockfile, and source files into a
single `.tar.zst`, `.tar.gz`, or `.tar.bz2` file. In a git repo, only
tracked files are included.

```bash
conda workspace archive -o my-project.tar.zst
```

The resulting file contains everything needed to reproduce the
workspace elsewhere:

```text
my-project.tar.zst
  conda.toml
  conda.lock
  src/
    app.py
    ...
```

If no `-o` is given, the archive is named after the workspace
(`<name>.tar.zst`) and placed in the project root.

## Use gzip compression

For broader compatibility, use a `.tar.gz` extension:

```bash
conda workspace archive -o my-project.tar.gz
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
conda workspace unarchive my-project.tar.zst
```

This creates a `my-project/` directory (derived from the archive
filename) containing the full workspace. To choose a different
location:

```bash
conda workspace unarchive my-project.tar.zst --target /path/to/destination
```

After extraction, install the environments from the lockfile:

```bash
cd my-project
conda workspace install --locked
```

## Extract and install in one step

![archive install demo](../../demos/archives-install.gif)

Pass `--install` to extract the archive and install all environments
from the lockfile in a single command:

```bash
conda workspace unarchive my-project.tar.zst --target /path/to/destination --install
```

This is equivalent to extracting, changing into the directory, and
running `conda workspace install --locked`, but without the extra steps.

## Lock before archiving

If your lockfile is out of date or does not exist yet, pass `--lock`
to solve and write `conda.lock` before creating the archive:

```bash
conda workspace archive --lock
```

This is equivalent to running `conda workspace lock` followed by
`conda workspace archive`, but in a single command.

## Bundle packages for offline use

![archive bundle demo](../../demos/archives-bundle.gif)

When the target machine has no internet access, use `--bundle` to
include all resolved conda package archives (`.conda` or `.tar.bz2`)
inside the archive.

You need a lockfile first. Pass `--lock` to generate one automatically,
or run `conda workspace lock` beforehand.

```bash
conda workspace archive --lock --bundle -o my-project-offline.tar.zst
```

This adds a `packages/` directory inside the archive. Package hashes
are verified against the lockfile before bundling.

On the receiving end, `conda workspace unarchive` detects the bundled
packages, verifies their hashes, and copies them into the local conda
cache before installation:

```bash
conda workspace unarchive my-project-offline.tar.zst
# packages are primed into the conda cache automatically
cd my-project-offline
conda workspace install --locked
```

Pass `--no-install` to skip cache priming if you only want the files:

```bash
conda workspace unarchive my-project-offline.tar.zst --no-install
```

## Security

Archives are extracted with path traversal protection. Every member is
validated before extraction: absolute paths, `..` components, symlinks
escaping the target directory, and special file types (device nodes,
FIFOs) are all rejected. On Python 3.12+ the `filter="data"` parameter
provides additional defense-in-depth.

When `--bundle` is used, package hashes are verified against the
lockfile's SHA256 entries both at archive creation and at extraction.
Packages without a SHA256 entry in the lockfile are rejected instead of
being copied into the conda package cache.

### Trust model for bundled archives

A bundled archive is *self-consistent*: the package hashes match the
lockfile that ships inside the archive. However, the lockfile itself is
not externally signed. If the archive came from an untrusted source, the
lockfile inside it could have been tampered with to match altered
packages.

To guard against this:

- Obtain archives only from trusted sources.
- When possible, compare the lockfile inside the archive against a
  separately obtained copy (e.g. from version control).
- Use `conda workspace install --locked` so the solver does not
  silently add packages beyond what the lockfile specifies.

## Next steps

- {ref}`Archive configuration <archive-configuration>` for all archive settings
- {ref}`Archives <archives>` for a feature overview
- [CLI reference](../reference/cli.md) for the full `archive` and
  `unarchive` command-line options
