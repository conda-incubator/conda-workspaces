# Workspace environments and features

Workspace environments are named conda prefixes composed from one or
more features. Features group dependencies, channels, PyPI dependencies,
activation settings, platform constraints, and system requirements so a
workspace can define several related environments in one manifest.

## Environments

![multi-env demo](../../demos/multi-env.gif)

By default, each environment is installed under `.conda/envs/<name>/`
in the workspace root.

```toml
[environments]
default = []
test = { features = ["test"] }
docs = { features = ["docs"] }
```

The `default` environment always exists. All environments inherit the
default feature, including the top-level `[dependencies]`, unless
`no-default-feature = true` is set for that environment.

:::{note}
Pixi's `solve-group` key is accepted in manifests for compatibility but
has no effect. Conda's solver operates on a single environment at a time
and does not support cross-environment version coordination. Each
environment is solved independently.
:::

## Features

Features are composable groups of dependencies, channels, and settings.
They map to `[feature.<name>]` tables in the manifest:

```toml
[feature.test.dependencies]
pytest = ">=8.0"
pytest-cov = ">=4.0"

[feature.docs.dependencies]
sphinx = ">=7.0"
myst-parser = ">=3.0"
```

When an environment includes multiple features, dependencies are merged
in order. Later features override earlier ones for the same package name.

## Workspace dependency inheritance

Use `[workspace.dependencies]` to centralize conda specs that multiple
dependency tables should share. A table entry opts in explicitly with
`{ workspace = true }`. Pixi added the same workspace dependency
inheritance syntax in 0.70.0, and conda-workspaces reads it from
`conda.toml`, `pixi.toml`, and supported `pyproject.toml` tables.

```toml
[workspace.dependencies]
numpy = "1.*"
cmake = { version = ">=3.28", channel = "conda-forge" }

[dependencies]
numpy = { workspace = true }

[feature.build.dependencies]
cmake = { workspace = true, build = "h*" }
```

The root spec supplies the version and any other base match fields. The
consuming entry may add non-version fields such as `build`, `channel`,
or `subdir`. Restating `version` alongside `workspace = true` is an
error.

## Channels

Channels are specified at the workspace level and can be overridden per
feature:

```toml
[workspace]
channels = ["conda-forge"]

[feature.special.dependencies]
some-pkg = "*"

[feature.special]
channels = ["conda-forge", "bioconda"]
```

Feature channels are appended after workspace channels, with duplicates
removed.

(platform-targeting)=

## Platform targeting

![multi-platform demo](../../demos/multi-platform.gif)

Per-platform dependency overrides use `[target.<platform>]` tables:

```toml
[dependencies]
python = ">=3.10"

[target.linux-64.dependencies]
linux-headers = ">=5.10"

[target.osx-arm64.dependencies]
llvm-openmp = ">=14.0"
```

Platform overrides are merged on top of the base dependencies when
resolving for a specific platform.

### Known vs. declared platforms

:::{versionadded} 0.4.0
`conda workspace info` surfaces the reachable platform set as a
`known_platforms` JSON key (and a matching `Known Platforms` row in
the text view whenever a feature broadens the workspace-level set).
`conda workspace lock --platform <subdir> --output <fragment>` validates
against this same set.
:::

The workspace-level `platforms` list is the default set every
environment can be solved for. Individual features may declare
additional platforms, and those are reachable through any
environment that activates that feature. To see the full reachable
set, run:

```bash
conda workspace info            # text view, extra "Known Platforms" row
conda workspace info --json     # JSON "known_platforms" key
```

`conda workspace lock --platform <subdir> --output <fragment>` validates
against this reachable set, so typos like `lixux-64` are rejected before the
solver runs. Filtered lock runs require a separate output file.

(pypi-dependencies)=

## PyPI dependencies

PyPI dependencies are specified separately from conda dependencies:

```toml
[pypi-dependencies]
my-local-pkg = { path = ".", editable = true }
some-pypi-only = ">=1.0"

[feature.test.pypi-dependencies]
pytest-benchmark = ">=4.0"
```

PyPI package names are translated to their conda equivalents via the
[grayskull mapping](https://github.com/conda/grayskull) and merged
into the same solver call as conda dependencies. `conda-pypi` delegates
to the configured solver backend to resolve conda and PyPI packages
together in a single pass and handles `.whl` installation.

To use PyPI dependencies you need:

- [conda-pypi](https://github.com/conda/conda-pypi) (`>=0.9.0`) for
  name mapping and wheel extraction
- [conda-rattler-solver](https://github.com/conda-incubator/conda-rattler-solver)
  as the solver backend (no longer a hard dependency of conda-pypi, so
  install it explicitly)
- The `conda-pypi` channel (`conda config --append channels conda-pypi`)
  which serves pure Python packages from PyPI as conda packages using
  sharded repodata (requires the rattler solver)

Local path dependencies (e.g. `path = "."`) are handled separately via
`conda-pypi`'s build system after the main solve completes. Git and URL
dependencies are parsed for pixi manifest compatibility but are not
installed yet. `conda workspace install` skips them with a warning. If
`conda-pypi` is not installed, version-only PyPI dependencies are skipped
with a warning, while local path dependencies raise an installation error.

See the [PyPI dependencies tutorial](../tutorials/pypi-dependencies.md)
for a full walkthrough including editable installs and troubleshooting.

## No-default-feature

An environment can opt out of inheriting the default feature:

```toml
[environments]
minimal = { features = ["minimal"], no-default-feature = true }
```

This is useful for environments that need a completely independent
dependency set.

## Activation

Features can specify activation scripts and environment variables:

```toml
[activation]
scripts = ["scripts/activate.sh"]
env = { MY_VAR = "value" }

[feature.dev.activation]
env = { DEBUG = "1" }
```

Activation settings are merged across features when composing an
environment. After `conda workspace install`, environment variables are
written to the prefix state file (available via `conda activate`) and
activation scripts are copied to `$PREFIX/etc/conda/activate.d/`.

## System requirements

System requirements declare minimum system-level dependencies:

```toml
[system-requirements]
cuda = "12"
glibc = "2.17"
```

System requirements are added as virtual package constraints
(`__cuda >=12`, `__glibc >=2.17`) during environment solving. This
ensures the solver only picks packages compatible with the declared
system capabilities.

## Channel priority

The workspace-level `channel-priority` setting overrides conda's global
channel priority during solving:

```toml
[workspace]
channels = ["conda-forge"]
channel-priority = "strict"
```

Valid values are `strict`, `flexible`, and `disabled`. When not set,
conda's default channel priority applies.
