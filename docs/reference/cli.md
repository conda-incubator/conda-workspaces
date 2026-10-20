# CLI reference

## conda workspace

`conda ws` is a shorthand alias for `conda workspace`. Both forms
accept the same subcommands, flags, and arguments.

### Structured output

Successful commands that advertise `--json` emit exactly one JSON
value on stdout. Successful `add`, `update`, `remove`, `platform add`,
`platform remove`, `install`,
`lock`, `clean`, `import`, `archive`, and `unarchive` operations
return:

```json
{"success": true}
```

Query and export commands return command-specific data. `quickstart`
returns its workspace, environment, manifest, added specs, and shell
state. `init`, `activate`, `run`, and `shell` tolerate a global
`--json` flag but intentionally emit no structured result.

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

(generated-file-permissions)=

### Generated file permissions

On POSIX systems, new manifests, lockfiles, export files, workspace archives,
OCI archive files, and receipts use mode `0600`, or a more restrictive mode
set by the process umask. Replacing an existing regular file preserves its
read, write, and execute permission bits. To share a generated file with
other users, set its permissions explicitly after writing it.

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

### Workspace launchers

`conda workspace ship` delegates a selected locked environment to conda-ship.
It requires `-e/--environment`, `--platform`, and `-o/--output DIR`. The
command reads the existing `conda.lock` without solving, installing a local
environment, or modifying the manifest or lockfile.

`--delegate-executable` overrides the executable configured in
`[tool.conda-ship]`. `--artifact-layout online|external|embedded` overrides the
manifest's layout. Runtime naming and version settings remain in
`[tool.conda-ship]`. The global `--file` option selects the exact source manifest.

`--dry-run` previews the build without downloading packages or writing
artifacts. `--json` emits one result on stdout with `success`, `environment`,
`platform`, `output`, and `dry_run`. `output` is the absolute output directory,
and `dry_run` indicates whether this was a preview. The detailed builder plan
and build diagnostics go to stderr. A failed builder preserves its exit code
and emits no success result. Named platform variants and local path, Git, or
URL PyPI dependencies are unsupported. No project source files or manifest
tasks are copied into the launcher.

The launcher installs its managed environment on first use. Online artifacts
download packages then, while external and embedded bundles download them
during the build. Install conda-ship 0.11.1 or later in the environment that
runs conda with `conda install -n base "conda-forge::conda-ship>=0.11.1"`,
replacing `base` if needed. See
[Ship a workspace environment](../how-to/ship.md) for prerequisites and examples.

### Importing manifests

Without `-e/--environment`, `conda workspace import SOURCE` converts a
supported source manifest into a complete new `conda.toml`. It does not merge
with an existing workspace, update `conda.lock`, or install an environment.
Use `-o/--output` to select another output path. The global `--file` option is
not accepted in this conversion mode.

When importing `pixi.toml` or `pyproject.toml`, the source must be a regular
file without symbolic links in its directory path. This also applies to
`--dry-run`.

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
