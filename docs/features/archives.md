(archives)=

# Archives and portable workspaces

Workspace archives package a project into a portable `.tar.zst`,
`.tar.gz`, or `.tar.bz2` archive that includes the manifest and source
files. An existing or newly generated lockfile can also be included,
along with resolved conda package artifacts for offline deployment and
receipts for integrity checks.

![archive demo](../../demos/archives.gif)

```bash
conda workspace archive -o my-project.tar.zst
```

When `-o` / `--output` is omitted, the archive is written in the
workspace root using the workspace name as the filename stem. That
default name must be a single filename segment. Use `-o` / `--output`
for other paths.

In git repos, only tracked files are included. Built-in exclusions
(`.git`, `__pycache__`, `.conda/envs`, `.pixi`, and common credential
material such as `.env`, `.ssh`, `.aws`, and `.npmrc`) always apply.
Configure additional exclusions in the manifest:

```toml
[workspace.archive]
exclude = ["docs/**", "*.log"]
```

Or pass them on the command line:

```bash
conda workspace archive --exclude "benchmarks/**"
```

## Restoring archives

Extract an archive and install environments in one step:

```bash
conda workspace unarchive my-project.tar.zst --target ./restored --install
```

The extraction target path must be absent. Existing files, links, and
directories, including empty directories, are not overwritten.

Install one archived environment to a final runtime prefix, optionally
under a staged filesystem root:

```bash
conda workspace unarchive my-project.tar.zst \
  --install \
  --dest /tmp/rootfs \
  -e runtime \
  --prefix /opt/runtime
```

When `--dest` is used, `unarchive` warns if installed files still
reference the physical staging prefix.

## Locking, bundling, and receipts

Pass `--lock` to solve and update the lockfile before archiving:

```bash
conda workspace archive --lock
```

For offline deployment, `--bundle` includes resolved conda package
archives (`.conda` or `.tar.bz2`) inside the archive. Package hashes
are verified against the lockfile on bundling and before receipt-verified
cache priming:

```bash
conda workspace archive --lock --bundle --receipt -o offline.tar.zst
conda workspace unarchive offline.tar.zst --receipt
```

For handoff workflows that need a separate integrity record, `--receipt`
writes an external in-toto Statement JSON file. `unarchive --receipt`
verifies the archive, extracted manifest, extracted lockfile, and
lockfile package inventory before moving the verified workspace into
place:

```bash
conda workspace archive --lock --receipt -o my-project.tar.zst
conda workspace unarchive my-project.tar.zst --receipt --target ./verified
```

Python integrations can use `conda_workspaces.archive.WorkspaceArchive`
for the same archive operations without importing CLI handlers.

See the [archive tutorial](../tutorials/archives.md) for a full CLI
walkthrough, [Use workspace archives from Python](../how-to/archive-api.md)
for integration examples, and the [archive receipt reference](../reference/archive-receipts.md)
for the receipt JSON format.
