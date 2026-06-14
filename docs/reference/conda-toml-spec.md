# `conda.toml` specification

This page is the normative reference for the `conda.toml` workspace
manifest format read by conda-workspaces.  It is the human-readable
companion to [`schema/conda-toml-1.schema.json`][schema] and the source
material for the future [Conda Enhancement Proposal][ceps] that will
standardise the format across the conda ecosystem.

For tutorials and examples see [Configuration](../configuration.md) and
[Quickstart](../quickstart.md). For plugin name aliases see [Plugin
format names and aliases](format-aliases.md).

[schema]: https://github.com/conda-incubator/conda-workspaces/blob/main/schema/conda-toml-1.schema.json
[ceps]: https://conda.org/learn/ceps/

## Status

| Field | Value |
|---|---|
| Format name | `conda.toml` |
| Schema version | `1` |
| Schema URL | `https://schemas.conda.org/conda-toml-1.schema.json` |
| Canonical plugin name | `conda-workspaces` (see [format-aliases.md](format-aliases.md)) |
| Reference implementation | [`conda_workspaces.manifests.toml.CondaTomlParser`](https://github.com/conda-incubator/conda-workspaces/blob/main/conda_workspaces/manifests/toml.py) |
| Standardisation track | Pre-CEP — see the [CEP tracker issue](https://github.com/conda-incubator/conda-workspaces/issues) |

## Scope

`conda.toml` describes a *workspace*: a project root that may declare
one or more conda environments composed from reusable *features*, plus
a set of *tasks* that run inside those environments.  It is the
conda-native sibling of `pixi.toml`. The core workspace, dependency,
feature, environment, and task tables deliberately overlap with pixi,
while conda-workspaces also owns conda-specific extensions such as
`default-environment` and `[workspace.archive]`. The reverse direction
(pixi.toml → conda.toml) holds only when the `pixi.toml` keeps to the
fields described here. See *Pixi compatibility ladder* below for the
precise asymmetry.

Out of scope: package build recipes (use `recipe.yaml`), package
distribution metadata (`pyproject.toml` `[project]`), and lockfile
contents (use the companion `conda.lock`, see *Lockfile relationship*
below).

## File detection

A conda-workspaces tool MUST detect a workspace using one of these two
forms, in order:

1. A file named **`conda.toml`** at the workspace root that contains a
   top-level `[workspace]` table.
2. A file named **`pyproject.toml`** at the workspace root that
   contains a `[tool.conda.workspace]` table.

A `conda.toml` without a `[workspace]` table is permitted: it is
treated as a *tasks-only* manifest (see `[tasks]` below) and does not
constitute a workspace on its own.

For compatibility, conda-workspaces also reads `pixi.toml` (top-level
`[workspace]` / `[project]`) and `pyproject.toml`
`[tool.pixi.workspace]`.  That compatibility surface is not part of
this specification — it is documented in
[Configuration](../configuration.md).

### Search order

Tools that search a directory tree for the manifest MUST consult these
filenames in this order, returning the first hit:

1. `conda.toml`
2. `pixi.toml`
3. `pyproject.toml`

When more than one form is present in the same directory, `conda.toml`
wins.  When `pyproject.toml` contains both `[tool.conda]` and
`[tool.pixi]` tables, `[tool.conda]` wins (see
[Configuration](../configuration.md)).

## Top-level tables

All tables are optional unless marked **required**.  Field types follow
TOML conventions: *string*, *integer*, *boolean*, *array*, *table*,
*inline-table*.

### `[workspace]` (required when used as a workspace manifest)

Workspace metadata.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | no | Workspace name.  Defaults to the workspace directory name. |
| `version` | string | no | Workspace version. |
| `description` | string | no | Short prose description. |
| `channels` | array of *channel* | **yes** | Conda channels in priority order. |
| `platforms` | array of *platform* | **yes** | Platforms the workspace targets.  Used to drive multi-platform solves. |
| `channel-priority` | string | no | One of `"strict"`, `"flexible"`, `"disabled"`.  Maps to conda's solver setting. |
| `envs-dir` | string | no | Where per-env prefixes live, relative to the workspace root.  Default: `.conda/envs`. |
| `dependencies` | conda deps | no | Root-level dependency pool used by `{ workspace = true }` entries in dependency tables. |
| `archive` | table | no | Archive filters and compression settings. See `[workspace.archive]`. |

A *channel* is either:

- a string containing a channel name (`"conda-forge"`) or URL
  (`"https://my.channel/label/dev"`), or
- an inline table `{ channel = "<name-or-url>" }`.  Other keys (e.g.
  `priority`) are reserved and currently ignored by this version.

A *platform* is either a conda subdir string from the closed enum
defined in the [JSON schema][schema] under `$defs.platform` (e.g.
`linux-64`, `osx-arm64`, `win-64`, `noarch`) or a Pixi-compatible
rich-platform inline table. Rich-platform tables may set
`platform = "<subdir>"`, an optional workspace-scoped `name`, and
virtual package constraints such as `cuda`, `archspec`, `glibc` /
`libc`, `linux`, `macos` / `osx`, `windows` / `win`, or raw
`__<virtual-package>` keys.

```toml
[workspace]
platforms = [
  "linux-64",
  { name = "linux-64-cuda", platform = "linux-64", cuda = "12.0" },
]
```

When `name` is omitted, conda-workspaces follows Pixi's
generated-name style, for example
`{ platform = "linux-64", cuda = "12.0" }` becomes
`linux-64-cuda-12-0`. The declared name is used by feature
`platforms` restrictions and as the `conda.lock` platform key. The
backing `platform` subdir is used for conda solves and package URL
validation.

### `[workspace.archive]`

Archive settings used by `conda workspace archive`.

| Field | Type | Description |
|---|---|---|
| `include` | array of string | Glob patterns for files to include. When set, only matching files are archived. |
| `exclude` | array of string | Glob patterns for files to exclude. |
| `compression` | string | One of `"zst"`, `"gz"`, or `"bz2"`. |
| `compression-level` | integer | Compression level passed to the selected compressor. |

### `[workspace.dependencies]`

Reusable conda package specs keyed by package name. Regular dependency
tables can opt into a root spec with `{ workspace = true }`, which keeps
shared versions in one place while preserving explicit package membership
in each feature or target table. This follows the workspace dependency
inheritance syntax that pixi added in 0.70.0.

```toml
[workspace.dependencies]
numpy = "1.*"
cmake = { version = ">=3.28", channel = "conda-forge" }

[dependencies]
python = ">=3.12"
numpy = { workspace = true }

[feature.build.dependencies]
cmake = { workspace = true, build = "h*" }
```

The inherited entry uses the root spec as a base. Non-version fields such
as `build`, `channel`, `subdir`, `md5`, `sha256`, `url`, `file-name`,
`license`, `license-family`, `features`, and `track-features` may be
set on the consuming dependency to layer on top. `version` is owned by
the workspace entry. Setting both `workspace = true` and `version` is an
error. `workspace = false` is also rejected.

### `[dependencies]`

Conda packages that belong to the *default feature*.  Keys are package
names. Values are either a version constraint string or a *detailed
dependency spec*. Detailed specs may also inherit from
`[workspace.dependencies]` with `{ workspace = true }`.

```toml
[dependencies]
python = ">=3.10"
numpy = ">=1.24,<2"
scipy = "*"
cuda-toolkit = { version = ">=12", build = "*cuda*" }
```

A detailed conda dependency accepts:

| Field | Type | Description |
|---|---|---|
| `version` | string | Version constraint (e.g. `">=12"`). |
| `build` | string | Build-string glob (e.g. `"*cuda*"`). |
| `build-number` | string | Build number constraint. |
| `channel` | string | Channel name or URL for this package. |
| `subdir` | platform | Platform/subdir constraint. |
| `md5` / `sha256` | string | Exact package hash. |
| `url` | string | Exact package URL. |
| `file-name` | string | Exact package filename. |
| `license` / `license-family` | string | License constraints. |
| `features` / `track-features` | array of strings | Feature constraints. |
| `workspace` | boolean | Must be `true`. Inherits from `[workspace.dependencies]`. Mutually exclusive with `version`. |

### `[pypi-dependencies]`

PyPI packages that belong to the default feature.  Keys are package
names. Values are either a PEP 508 version-constraint string or a
*detailed PyPI dependency spec*:

| Field | Type | Description |
|---|---|---|
| `version` | string | PEP 508 constraint. |
| `extras` | array of string | Extras to request, e.g. `["http2"]`. |
| `path` | string | Local path to the package. |
| `editable` | boolean | Install in editable mode. |
| `git` | string | Git repository URL. |
| `branch` | string | Branch (with `git`). |
| `tag` | string | Tag (with `git`). |
| `rev` | string | Revision (with `git`). |
| `url` | string | Direct package URL. |

### `[activation]`

Default-feature activation settings.

| Field | Type | Description |
|---|---|---|
| `scripts` | array of string | Shell scripts sourced on environment activation. |
| `env` | table of `string -> string` | Environment variables set on activation. |

### `[system-requirements]`

Default-feature system-level constraints (e.g. minimum glibc, macOS
version). Recognised keys mirror conda's virtual-package names
(`__glibc`, `__osx`, `__cuda`, …). Values are stringified version
constraints, except `libc` may also use pixi's inline table form
`{ family = "glibc", version = "2.28" }`.

### `[target.<platform>]`

Per-platform overrides for the default feature.  `<platform>` may be
a declared platform name or the backing conda subdir of a declared rich
platform. When both match, the subdir target is merged first and the
declared rich-platform name can override it.

| Field | Type | Description |
|---|---|---|
| `dependencies` | conda deps | Platform-specific conda overrides. |
| `pypi-dependencies` | PyPI deps | Platform-specific PyPI overrides. |

### `[feature.<name>]`

A reusable group of dependencies and settings.  `<name>` is an
arbitrary identifier referenced from `[environments]`.

| Field | Type | Description |
|---|---|---|
| `dependencies` | conda deps | Conda dependencies for this feature. |
| `pypi-dependencies` | PyPI deps | PyPI dependencies for this feature. |
| `channels` | array of *channel* | Additional channels. |
| `platforms` | array of string | Restrict the feature to declared platform names or subdirs. |
| `system-requirements` | table | Per-feature system requirements. |
| `activation` | table | Per-feature activation. |
| `target` | table of `[target.<platform>]` | Per-platform dep overrides for the feature. |
| `tasks` | table | Tasks contributed by this feature and merged into the workspace task set. |

### `[environments]`

A named environment is a composition of one or more features plus
(by default) the *default feature* defined by the top-level
`[dependencies]`, `[pypi-dependencies]`, `[activation]` and
`[system-requirements]` tables.

Two forms are accepted:

```toml
[environments]
test = ["test"]                                     # shorthand
docs = { features = ["docs"], no-default-feature = false }
```

| Field | Type | Description |
|---|---|---|
| `features` | array of string | Feature names to include in addition to the default feature. |
| `solve-group` | string | Accepted for pixi compatibility. Currently ignored. Environments are solved independently. |
| `no-default-feature` | boolean | If `true`, exclude the default feature from this environment.  Default: `false`. |

When `[environments]` is omitted entirely, a single implicit
environment named `default` is used, composed from the default feature
only.

### `[tasks]`

Named tasks runnable via `conda task run <name>`.  Each value is
either a command string or a *task table*.

| Field | Type | Description |
|---|---|---|
| `cmd` | string or array of string | Command to execute.  Omit to define an alias whose only purpose is `depends-on`. |
| `args` | array of *task arg* | Named arguments with optional defaults. |
| `depends-on` | array of *task dep* | Tasks to run before this one. |
| `cwd` | string | Working directory for the task. |
| `env` | table of `string -> string` | Environment variables to set. |
| `description` | string | Human-readable description. |
| `inputs` | array of string | Glob patterns for cache inputs. |
| `outputs` | array of string | Glob patterns for cache outputs. |
| `clean-env` | boolean | Run with a minimal environment. |
| `default-environment` | string | Conda environment to activate by default for this task. |
| `target` | table of `[target.<platform>.tasks]` | Per-platform overrides. |

A *task arg* is an inline table such as
`{ arg = "path", default = "tests/" }`.

A *task dep* is one of:

- a string naming another task: `"build"`, or
- an inline table with extra fields:
  `{ task = "test", args = ["tests/unit/"], environment = "py311" }`.

### `[target.<platform>.tasks]`

Platform-specific task overrides.  Each entry follows the same shape
as a `[tasks]` entry.

## Composition rules

When a tool resolves an environment named `<env>`:

1. Start with the default feature (top-level `[dependencies]`,
   `[pypi-dependencies]`, `[activation]`, `[system-requirements]`,
   `[target]`) unless the environment sets `no-default-feature = true`.
2. Merge in each named feature listed in `features`, in order.  Later
   features override earlier ones for conflicting keys. Lists are
   concatenated and de-duplicated.
3. Apply `[target.<platform>]` overrides for the host's platform last.

Channel order is preserved.  Duplicate dependency names within the
same stack (conda or PyPI) are an error and the tool MUST surface them
to the user.

## Lockfile relationship

`conda workspace lock` and `conda workspace install` produce and
consume **`conda.lock`** at the workspace root.  The lockfile schema is
derived from [rattler-lock v6][rattler-lock] (the same schema
`pixi.lock` uses) with one on-disk difference: `conda.lock` writes
`version: 1` instead of `version: 6` so tools can identify the file as
conda-workspaces-owned at a glance.  The remainder of the document —
`environments`, `packages`, channels, platform package lists — is
structure-compatible with rattler-lock v6.

See [Plugin format names and aliases](format-aliases.md) for the
canonical and alias strings under which `conda.lock` is registered.

[rattler-lock]: https://github.com/conda/rattler/tree/main/crates/rattler_lock

### Lockfile satisfiability check

`conda workspace install` and `conda workspace info` evaluate the
lockfile against the manifest to determine whether a re-solve is
needed. The check is purely structural (no file timestamps) and
runs in this order:

1. **Schema version** -- the lockfile `version` field must match the
   expected version (`1`).
2. **Environments** -- every environment declared in the manifest must
   have a corresponding entry in the lockfile.
3. **Channels** -- the channel list for each environment in the lockfile
   must match the manifest (order-sensitive, URLs normalized).
4. **Platforms** -- every platform declared in the manifest must be
   present in each lockfile environment's `packages` section.
5. **Dependencies** -- for each dependency in the manifest (on the
   current platform), a locked package must exist whose version
   satisfies the manifest's version spec.

The check returns the first failing condition it finds. The result
is one of three states:

| Status | Constant | Meaning |
|---|---|---|
| `up-to-date` | `LockfileStatus.UP_TO_DATE` | All checks pass |
| `out-of-date` | `LockfileStatus.OUT_OF_DATE` | At least one check fails |
| `missing` | `LockfileStatus.MISSING` | No `conda.lock` file found |

### JSON output fields

`conda workspace info --json` includes the following lockfile fields:

| Field | Type | Description |
|---|---|---|
| `lockfile_status` | `string` | One of `up-to-date`, `out-of-date`, `missing` |
| `lockfile_reason` | `string` | Human-readable reason (only present when `out-of-date`) |

## Embedded form (`pyproject.toml`)

The same tables are accepted under `[tool.conda.<name>]` inside a
`pyproject.toml`:

| Top-level table | Embedded equivalent |
|---|---|
| `[workspace]` | `[tool.conda.workspace]` |
| `[dependencies]` | `[tool.conda.dependencies]` |
| `[pypi-dependencies]` | `[tool.conda.pypi-dependencies]` |
| `[activation]` | `[tool.conda.activation]` |
| `[system-requirements]` | `[tool.conda.system-requirements]` |
| `[target.<platform>]` | `[tool.conda.target.<platform>]` |
| `[feature.<name>]` | `[tool.conda.feature.<name>]` |
| `[environments]` | `[tool.conda.environments]` |
| `[tasks]` | `[tool.conda.tasks]` |

When both `conda.toml` and `pyproject.toml` exist in the same
directory, `conda.toml` wins.  When a `pyproject.toml` contains both
`[tool.conda]` and `[tool.pixi]` tables, `[tool.conda]` wins.

## Pixi compatibility ladder

`conda.toml` and `pixi.toml` share a deliberately overlapping table
layout, but they are not byte-for-byte interchangeable.  The
relationship is:

| Direction | Holds? | Why |
|---|---|---|
| Every valid `conda.toml` is a valid `pixi.toml` (modulo filename). | **No** | The core workspace/dependency/task fields overlap intentionally, but conda-workspaces extensions such as `default-environment` and `[workspace.archive]` are not pixi schema guarantees. |
| Every valid `pixi.toml` is a valid `conda.toml`. | **No** | Pixi's `[workspace]` accepts additional fields (`authors`, `license`, `readme`, `homepage`, `repository`, `documentation`, `requires-pixi`, `preview`, `build-variants`, `conda-pypi-map`, …) that this v1 schema does not list. The schema is a strict validation target and rejects unknown keys (`additionalProperties: false`). |
| pixi reads our `default-environment` task field. | **No** | `default-environment` (in `[tasks]` and `[feature.*.tasks]`) is a conda-workspaces extension. Pixi may ignore it. |
| conda-workspaces reads `pixi.toml`. | **Yes** | conda-workspaces ships a separate compatibility reader for `pixi.toml` and `[tool.pixi.*]`. That reader is *out of scope* for this spec and is documented in [Configuration](../configuration.md). |

Two intentional differences from `pixi.toml`:

- `conda.toml` accepts only `[workspace]`. The legacy pixi `[project]`
  table is not part of this specification.
- conda-workspaces resolves dependencies with conda's configured solver
  backend and installs them as conventional conda prefixes. Pixi's
  build-process options (e.g. `[package]`-style build recipes) are not
  part of this specification.

If you need pixi-only fields, write a `pixi.toml` and let
conda-workspaces' compatibility reader handle it. Do not extend
`conda.toml` with them.

## Versioning policy

The schema is versioned with a single integer (`1`).  A future
backwards-incompatible change to required fields, semantics, or
defaults bumps the version to `2` and gets a sibling schema URL
(`https://schemas.conda.org/conda-toml-2.schema.json`) and plugin
canonical name (`conda-workspaces-v2`).  Aliases follow the rules in
[`format-aliases.md`](format-aliases.md).

Backwards-compatible additions (new optional fields, new platforms in
the *platform* enum) do *not* bump the version.  The JSON schema is the
strict validation target for this version. The conda-workspaces runtime
parser is intentionally more permissive for pixi and forward
compatibility and may ignore unknown fields when reading manifests.

## Open questions (non-normative)

These are tracked for the CEP draft and are not part of `v1`:

- Whether `[workspace]` should be required to carry `version` and
  `name` (currently both optional).
- Whether `solve-group` should ever gain conda-workspaces semantics, or
  remain an accepted no-op compatibility field.
- Whether to standardise an `[envs]` shorthand that maps a feature
  one-to-one to an environment of the same name.
- Discovery rules for nested workspaces (currently parent-directory
  search stops at the first match).
- Whether `[tool.pixi.*]` compatibility belongs in this specification
  or in a separate "compatibility" CEP.
- Whether `conda.toml` should exist as a separate filename at all, or
  whether the spec should describe a "conda-workspaces dialect of
  `pixi.toml`" — i.e. a `pixi.toml` plus conda-workspaces extensions
  such as `default-environment` and `[workspace.archive]`, without a
  parallel filename or schema URL.  Trade-off: governance ownership
  (conda community vs. Prefix.dev) versus format proliferation.  See
  the CEP tracker for discussion.
