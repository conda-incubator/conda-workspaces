# CLI reference

## conda workspace

`conda ws` is a shorthand alias for `conda workspace`. Both forms
accept the same subcommands, flags, and arguments.

### Structured output

Successful commands that advertise `--json` emit exactly one JSON
value on stdout. Successful `add`, `update`, `remove`, `platform add`,
`platform remove`, `install`,
`lock`, `clean`, `import`, and `archive` operations return:

```json
{"success": true}
```

Query and export commands return command-specific data. `quickstart`
returns its workspace, environment, manifest, added specs, and shell
state. `init`, `activate`, `run`, and `shell` tolerate a global
`--json` flag but intentionally emit no structured result.

For an archive URL, `quickstart` also returns `success`, `source`, `artifact`,
`receipt`, `verified`, `dry_run`, `installed`, `primed_packages`,
`cache_priming_skipped`, and `return_code`. `artifact` is the downloaded
archive's filename. `source` and `receipt` omit credentials, tokens, query
strings, and fragments. `workspace` is the restored destination, and no
temporary download path is exposed. `manifest` is null for an archive preview.
JSON output suppresses the interactive shell and keeps recovery instructions
inside structured output on failure. A nonzero installation result includes
`recovery` commands and their `recovery_shell` (`sh` or `cmd.exe`). Exceptions
use conda's structured error output with recovery hints.

`unarchive` returns `success`, `source`, `target`, `receipt`, `verified`,
`dry_run`, `installed`, `environment`, `primed_packages`,
`cache_priming_skipped`, and `return_code`. `source` and `receipt` describe the
original inputs with credentials, tokens, query strings, and fragments removed.
The result does not expose temporary download paths. `target` is the resolved
destination. `receipt` is `null` when no receipt was selected, and `verified`
reports whether receipt verification succeeded. `installed` remains false for
extraction-only operations and previews.

`conda workspace sbom --json` returns `success`, `format`, and `environment`.
It adds `content` when writing to stdout or previewing a file with `--dry-run`.
After a file is written, it returns the path in `file` instead:

```json
{
  "success": true,
  "format": "cyclonedx-json-v1.7",
  "environment": "default",
  "file": "exports/default.cdx.json"
}
```

(archive-downloads)=

### Restoring archives

`conda workspace unarchive ARCHIVE` accepts a local path or an HTTP or HTTPS
URL. The URL path must end in a valid archive filename, such as
`example.tar.zst`, `example.tar.gz`, or `example.tar.bz2`. Response headers do
not change that filename. Remote archives are downloaded into private temporary
storage and pass through the same archive validation and extraction as local
files. The default target is a directory named after the archive in the current
directory.
`--target PATH` selects another destination. The target must not already exist.

```bash
conda workspace unarchive https://example.org/workspaces/example.tar.zst --target ./example
```

Extraction does not install environments. Add `--install` to install from the
included lockfile, and `-e/--environment` to select one environment.

Use `--receipt` to require the conventional sibling receipt,
`--receipt PATH` for a local receipt, or `--receipt URL` for an explicitly
located remote receipt. Without `--receipt`, unarchive does not request a
sibling receipt. See [Archive receipt reference](archive-receipts.md) for
selection, verification, and trust rules.

Downloads use conda's authentication and TLS settings. Redirects may switch
between HTTP and HTTPS, following conda's behavior, but cannot use other
schemes. Configure credentials through conda's existing authentication
facilities before retrieving an archive from a private host.

Connection retries use conda's settings. HTTP error responses abort the
download without automatic status retries, which could otherwise consume an
unbounded error body before the download size checks run.

Servers must send archive and receipt files without HTTP content compression.
Downloads request `Accept-Encoding: identity` and reject other HTTP
`Content-Encoding` values so download size limits apply to the transferred
bytes. The archive file's own compression, such as `.tar.gz` or `.tar.zst`,
remains supported.

`--dry-run` downloads and validates remote inputs in temporary storage. It
leaves the target and environment prefixes unchanged. With `--install`,
validation can also download packages into a temporary cache. Temporary
downloads are removed when the operation finishes or fails.

### Quickstart from an archive URL

```bash
conda workspace quickstart https://example.org/workspaces/example.tar.zst
```

This restores a new workspace, installs the selected environment from its
published `conda.lock`, and opens a shell in that workspace. The default
environment is `default`. Select another with `-e/--environment`.
The manifest, lockfile, and project files are preserved. A missing or stale
lockfile stops the operation instead of triggering a new solve.

The positional arguments determine which quickstart flow runs:

| Input | Behavior |
| --- | --- |
| One HTTP or HTTPS URL ending in `.tar.zst`, `.tar.zstd`, `.tar.gz`, or `.tgz` | Restore a workspace archive |
| Package specs, including URLs ending in `.conda` or `.tar.bz2` | Add packages to a workspace in the current directory |
| `--archive` with one archive URL | Restore a workspace archive, including `.tar.bz2` |

Filename matching uses the decoded URL path, excluding queries and fragments.
Use `--archive` for a workspace archive with the `.tar.bz2` suffix, which is
also used by conda packages:

```bash
conda workspace quickstart --archive https://example.org/workspaces/example.tar.bz2
```

Archive URLs cannot be combined with extra positional arguments. Quickstart
does not probe a server or retry a failed archive operation as a package
operation to decide how to interpret an input.

By default, the URL filename without its archive suffix names a new directory
under the current directory. The derived name must be a valid directory name.
`--target PATH` selects a different destination. An existing destination,
including an empty directory or symlink, is rejected. Archive metadata and
redirects cannot change the destination.

Archive quickstart rejects `--copy`/`--clone`, `--file`, `--format`, `--name`,
`--platform`, `-c/--channel`, `--override-channels`, `--force-reinstall`, and
`--frozen`. These options would select another source or change the published
workspace. `--locked` is accepted but unnecessary because archive installation
always uses the lockfile. `--copy` and `--clone` remain local manifest-copy
operations.

`--receipt`, `--receipt PATH`, and `--receipt URL` use the
{ref}`archive receipt rules <remote-archive-receipts>`.
`--require-sha256` requires a receipt and checks its conda package records.
`--allow-large-archive` raises the usual archive size limit.
These archive options and `--target` require an archive URL.

`--no-shell` suppresses the shell without skipping installation. `--json`
also suppresses the shell. `--dry-run` downloads and validates the archive
and selected environment without creating the destination. See
{ref}`the quickstart guide <quickstart-workspace>` for recovery
after an installation or shell failure.

### Locked installation

`conda workspace install --locked` requires a current lockfile. `--frozen`
uses the existing lockfile without checking whether the manifest has changed.

Both modes accept `--platform NAME` to select a declared platform variant for
the current machine. Its conda subdir must match the native platform. This
option does not enable installation for a different operating system or
architecture.

`-p/--prefix PATH` installs the selected environment at an explicit prefix.
It requires `-e/--environment` and either `--locked` or `--frozen`.

Add `--download-only` to validate and fetch locked packages without creating,
removing, or updating environment prefixes or building local Python packages.
It requires `--locked` or `--frozen`. Locked installation with `--dry-run`
fetches packages into a temporary cache to complete validation without changing
the environment, with or without `--download-only`.

(feature-platforms)=
### Platform management

Use `platform add` and `platform remove` to edit the workspace platform list.
Add `--feature NAME` to edit an existing named feature instead:

```bash
conda workspace platform add linux-64 win-64
conda workspace platform remove win-64
conda workspace platform add --feature tools linux-64
conda workspace platform remove --feature tools linux-64
conda workspace --file pyproject.toml platform add win-64
```

Both commands accept one or more platform names. Without `--feature`, they edit
`[workspace].platforms`, including legacy `[project].platforms` in `pixi.toml`.
With `--feature`, they edit `[feature.NAME].platforms`. For `pyproject.toml`,
they edit the corresponding selected `[tool.conda]` or `[tool.pixi]` tables.
They do not create features. Omit `--feature` to change workspace platforms
instead of using the unsupported `--feature default`.

Added names must be concrete platforms recognized by conda, or custom names
already declared in the workspace. `noarch` is a package category, not a solve
target, so it cannot be added, including through a custom name. Typo suggestions
are advisory. Adding an already listed platform has no effect. Removing a
platform that is not explicitly listed in the selected table is an error.

Workspace changes affect environments that derive their platforms from the
workspace. Explicit feature platform lists are unchanged.
Removing the last workspace platform retains `platforms = []`, so environments
that inherit it use the native platform. Existing rich platform entries keep
their subdir mappings, system requirements, and comments. A workspace definition
with a custom subdir mapping or system requirements cannot be removed while a
feature still references it. Remove those feature references first. An ordinary
platform such as `linux-64` can remain in a feature after removal from the
workspace list.

Changing a shared feature affects every environment that includes it. Adding
the first explicit platform starts a new list with the requested names. It
does not copy the inherited workspace platforms. Removing the last explicit
platform removes the setting, so environments derive their supported platforms
from their other features or the workspace.

By default, these commands update `conda.lock` without installing or changing
any environment prefix. Existing solutions for retained targets stay unchanged,
new targets are solved, and removed targets are dropped. If the lockfile is
stale, refresh it with `conda workspace lock` before editing platforms.
A missing lockfile is generated for the complete resulting workspace.

Use `--no-lockfile-update` to edit only the manifest. `--dry-run` validates
and previews the change without writing the manifest, lockfile, or prefixes.
Combining both flags previews only the manifest change.

The dependency command `conda workspace add --platform` continues to select
a platform-specific dependency table. It does not declare platform support.

### Workspace images

`conda workspace image` builds one selected Linux environment and its project
files using Docker Buildx. It requires `-e/--environment`, `--platform`, a
startup command after `--`, and one of `--load`, `--push`, or `-o/--output`.
Loading and pushing also require `-t/--tag`. Tags may be repeated.

Applications can run from copied project files. Python path, Git, and URL
dependencies and editable installs are unsupported. Version-based PyPI
dependencies use the existing conda-pypi integration.

`--base-image` selects the Linux base. `--builder` reuses an existing Buildx
builder. `--dry-run` previews the recipe and inputs without invoking Docker.
`--json` returns the environment, conda and OCI platforms, tags, destination,
and resulting image digests. See [Build a workspace image](../how-to/image.md)
for the complete workflow and source restrictions.

### Importing manifests

Without `-e/--environment`, `conda workspace import SOURCE` converts a
supported source manifest into a complete new `conda.toml`. It does not merge
with an existing workspace, update `conda.lock`, or install an environment.
Use `-o/--output` to select another output path. The global `--file` option is
not accepted in this conversion mode.

Use `-e/--environment` to import one `environment.yml` or `environment.yaml`
as a new named environment in an existing workspace:

```bash
conda workspace import -e test environment.yml
conda workspace --file path/to/pixi.toml import -e test environment.yml
```

The positional path is always the source. The global `--file` option selects
the exact `conda.toml`, `pixi.toml`, or supported `pyproject.toml` to modify.
`-o/--output` cannot be combined with `-e/--environment`.

The imported conda and PyPI dependencies are private to the new environment,
which does not inherit the workspace's default dependencies. If the source
omits `channels` or `platforms`, the new environment uses the workspace
values. An explicit channel list must match the workspace channels in the
same normalized order, and the `nodefaults` marker is rejected. An explicit
platform list must match the declared workspace platform names.

Named import updates the complete `conda.lock` and installs the new environment
by default. `--no-install` updates the manifest and complete lockfile without
changing a prefix. `--no-lockfile-update` updates only the manifest.
`--force-reinstall` permits replacement of an existing inactive target prefix
and cannot be combined with either opt-out. Use `--dry-run` to validate and
solve the prospective workspace without writing the manifest, lockfile, or
prefix.

### Workspace snapshot

`conda workspace info --json` returns the workspace metadata and lock
status together with `environment_details`. Each environment entry
includes its declared features, `no_default_feature`, prefix,
installation state, channels, supported platforms, and one resolution
for every platform. Resolved conda and PyPI dependencies include the
winning manifest table:

```json
{
  "spec": "python >=3.12 py*",
  "provenance": {
    "table": "[environments.test.dependencies]",
    "location": {
      "environment": "test",
      "feature": null,
      "platform": null
    },
    "inherited_from": "[workspace.dependencies]"
  }
}
```

Use `provenance.location` to build the `--environment`, `--feature`, and
`--platform` selectors accepted by `workspace remove` and, for conda
dependencies, `workspace update`. PyPI removals also need `--pypi`, identified
by the containing `pypi_dependencies` collection. `environment` and `feature`
are mutually exclusive, and all three fields are `null` for a top-level default
declaration. `platform` is the manifest target key accepted by `--platform`,
including a rich workspace platform name rather than only its resolved conda
subdir. `table` is the human-readable manifest location and should not be
parsed by programs.

`inherited_from` appears only when the winning conda declaration uses
`{ workspace = true }`. In that case, `location` still identifies the
declaration selected for mutation. Rich platform entries report both the
declared platform name and its conda subdir. PyPI specs use their
manifest-compatible structured form so Git selectors and editable paths remain
available to consumers.

Pass `--packages` to include each installed prefix's package records:

```bash
conda workspace info --json --packages
```

Without `--json`, `--packages` appends package tables to the workspace
overview.

This single call replaces separate `workspace envs`,
`workspace info -e`, and `workspace list -e` calls for every
environment. Without `--packages`, package records are omitted.
Uninstalled environments have an empty `packages` list when package
records are requested.

Auto-generated from the `conda workspace` argument parser.

```{eval-rst}
.. argparse::
   :module: conda_workspaces.cli
   :func: generate_workspace_parser
   :prog: conda workspace
```

## conda task

Auto-generated from the `conda task` argument parser.

```{eval-rst}
.. argparse::
   :module: conda_workspaces.cli
   :func: generate_task_parser
   :prog: conda task
```
