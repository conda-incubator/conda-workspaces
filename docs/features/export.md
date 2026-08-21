# Export and format interoperability

## CycloneDX SBOMs with conda-sboms

`conda workspace sbom` exports one resolved workspace environment as a
CycloneDX 1.7 JSON software bill of materials. It is a focused interface to the
optional [`conda-sboms`](https://github.com/jezdez/conda-sboms) exporter. The
SBOM mapping, validation, and serialization stay in conda-sboms rather than
conda-workspaces.

Install conda-sboms in the same environment that owns the `conda` executable
and conda-workspaces:

```console
conda activate base
conda pypi install "conda-sboms>=0.2.0"
```

If `conda pypi` is unavailable, install the wheel in that same environment:

```console
python -m pip install "conda-sboms>=0.2.0"
```

Without metadata flags, the command reports the missing
`cyclonedx-json-v1.7` exporter when the plugin is not installed. Per-export
metadata flags require conda-sboms 0.2.0 or newer.

The shortest form reads exact package records for the `default` environment
from the existing `conda.lock`, selects the host platform, and writes the SBOM
to standard output:

```console
conda workspace sbom
```

Pass `--file` to write the same document to a file, or select another
environment and platform explicitly:

```console
conda workspace sbom \
  --environment test \
  --platform linux-64 \
  --file exports/test-linux-64.cdx.json
```

The command does not solve or update the lockfile. Run `conda workspace lock`
first when `conda.lock` is missing or stale. It reconstructs package records
from metadata already stored in the lockfile and does not download or extract
package archives. A lockfile export combines its exact resolved records with
the selected environment's direct conda requirements from the manifest. Those
authoritative requested roots let conda-sboms connect the SBOM root to what the
workspace declares instead of inferring roots from the resolved dependency
graph. The command rejects the export when an exact record no longer satisfies
a direct manifest requirement.

For a rich workspace platform, `--platform` accepts either its declared name
or its backing conda subdir. For example, a `linux-64-cuda` lock entry backed
by `linux-64` can be selected with either value. When multiple declared
platforms share a backing subdir, use the declared name to choose one.

Use `--from-prefix` to inventory the selected installed workspace prefix:

```console
conda workspace sbom \
  --environment test \
  --from-prefix \
  --file exports/test-installed.cdx.json
```

Prefix mode uses conda history for requested roots and does not replace them
with manifest requirements. It supports only the host platform. Passing a
different `--platform` fails rather than describing the prefix as another
platform.

### Product and author metadata

Without product metadata, the CycloneDX root describes the selected conda
environment. Supply identities only when you can establish them for the
product and the people or organizations responsible for the SBOM:

```console
conda workspace sbom \
  --file exports/acme-runtime.cdx.json \
  --product-name "Acme Runtime" \
  --product-version "2026.08" \
  --product-manufacturer "Acme GmbH" \
  --product-manufacturer-url "https://acme.example" \
  --author-name "Alice Example" \
  --author-email "alice@acme.example" \
  --author-organization "Acme Product Security" \
  --author-organization-url "https://acme.example/security"
```

The product name and version must be supplied together. A manufacturer
requires both product values, and a manufacturer URL requires the manufacturer
name. An author email requires an author name. An author organization URL
requires the author organization name.

When no metadata flags are passed, conda-sboms reads its active conda plugin
settings. Passing any product, manufacturer, or author flag replaces those
settings for this export. Unspecified flag values remain unset rather than
being inherited from the active configuration. conda-workspaces does not infer
a manufacturer or author from the workspace, package records, or channels.

### Reproducible timestamps

conda-sboms uses the current UTC time by default. Set `SOURCE_DATE_EPOCH` to a
non-negative Unix timestamp when the same inputs must produce a stable
timestamp:

```console
SOURCE_DATE_EPOCH=1787184000 conda workspace sbom \
  --file exports/default.cdx.json
```

An invalid, negative, or unsupported value fails before output is written.
Byte-for-byte reproducibility also requires unchanged inputs and the same
serializer version.

### Generic export form

The SBOM command presets the existing environment exporter path to the
versioned `cyclonedx-json-v1.7` format and a single platform. The underlying
exporter is also available through the generic command:

```console
conda workspace export \
  --environment default \
  --from-lockfile \
  --platform linux-64 \
  --format cyclonedx-json-v1.7 \
  --file exports/default-linux-64.cdx.json
```

The generic command reads product and author values from conda-sboms plugin
settings. It leaves lockfile roots unchanged, so conda-sboms infers graph roots
instead of receiving the selected environment's manifest requirements. Use
`conda workspace sbom` for workspace roots and for explicit metadata flags
without changing plugin configuration.

### Coverage limits

The current lockfile conversion rejects a selected environment and platform
that contains pip or other external package references before conda-sboms
runs. Prefix history may also omit detected pip packages. The SBOM therefore
describes the resolved conda package graph supplied to the exporter, not every
component present in a workspace or installed prefix.

Conda package metadata does not identify every operating-system component or
dependency vendored or statically linked inside a package. The generated SBOM
is useful technical documentation, but it does not establish complete product
coverage or Cyber Resilience Act conformity. See the
[conda-sboms coverage guide](https://jezdez.github.io/conda-sboms/explanation/coverage-and-compliance/)
for the exact format and compliance boundaries.

## Generic environment and manifest exports

`conda workspace export` converts a workspace environment into any
format registered through conda's `conda_environment_exporters` plugin
hook. The same exporter surface is available through `conda export`, so
conda-workspaces does not need separate writers for each output format.

![export demo](../../demos/export.gif)

:::{versionadded} 0.4.0
`conda workspace export` plugs into conda's
`conda_environment_exporters` plugin hook, so every format
reachable through `conda export` and anything registered by a
third-party plugin such as `conda-lockfiles` is also reachable
through `conda workspace export`. `--from-lockfile` and
`--from-prefix` select alternative sources. `--platform`
(repeatable) drives multi-platform exports for exporters that opt
into `multiplatform_export`.
:::

The built-in exporter choices include the `environment-yaml` /
`environment-json` exporters, the `conda-workspaces-lock-v1` exporter
registered by conda-workspaces itself, the `conda-toml` / `pixi-toml` /
`pyproject-toml` manifest exporters, and any third-party exporter such
as `conda-lockfiles`' rattler-lock-v6 the moment it is installed.

```bash
# Default: environment-yaml from the declared manifest (no install needed)
conda workspace export -e default --file exports/environment.yml

# environment.json, format auto-detected from the filename
conda workspace export -e default --file exports/environment.json

# Export the selected environment as a new conda.toml manifest
conda workspace export --format conda-toml --file exports/conda.toml

# Export the same flattened environment under [tool.conda]
conda workspace export --format pyproject-toml --file exports/pyproject.toml

# Re-emit exact package records from an existing conda.lock
conda workspace export --from-lockfile --format conda-workspaces-lock-v1 \
    --platform linux-64 --platform osx-arm64 --file exports/conda.lock

# Export environment.yml from an existing conda.lock
conda workspace export --from-lockfile --file exports/environment.yml

# Mirror ``conda export`` semantics on an installed prefix
conda workspace export --from-prefix --no-builds --from-history

# Select pixi.toml as the exact source and write somewhere else
conda workspace --file path/to/pixi.toml export \
    --file ../exports/environment.yml
```

The global `--file` before `export` selects the source manifest. The
subcommand's `--file` selects the export destination.

Three sources feed the exporter:

- `Declared` (default) resolves the declared specs from the
  manifest per platform. No solver, no installed environment required
  — this is what makes the command useful before the first
  `conda workspace install`.
- `--from-lockfile` reconstructs `Environment` objects from an
  existing `conda.lock` via the `CondaLockLoader`.
- `--from-prefix` reads the live installed prefix the same way
  `conda export` does, so `--no-builds`, `--ignore-channels`, and
  `--from-history` behave identically.

Use `conda workspace lock` when the goal is to solve the complete workspace
and update its canonical `conda.lock`. Export does not run the solver.

`--platform` (repeatable) intersects declared or available platforms
with the chosen subset. Passing multiple platforms requires an exporter
that opts into `multiplatform_export`. The `conda-workspaces-lock-v1`,
rattler-lock-v6, and the three manifest exporters (`conda-toml`,
`pixi-toml`, `pyproject-toml`) do. The single-platform YAML and JSON
exporters raise a clear error.

## Manifest-format exporters

:::{versionadded} 0.4.0
Three new exporter plugins — `conda-toml`, `pixi-toml`, and
`pyproject-toml` — write one selected environment in any manifest
dialect conda-workspaces already reads.
:::

The manifest exporters flatten one selected environment. They preserve its
declared dependencies across the requested platforms, but do not preserve the
source workspace's named features, other environments, tasks, activation
settings, or archive configuration. Write to a new file unless replacing that
structure with a single flattened environment is intentional. Specs that appear
on every requested platform land under the top-level `[dependencies]` /
`[pypi-dependencies]` tables. Platform-specific deltas move under
`[target.<platform>.*]`.
The `pyproject-toml` exporter wraps the same content under
`[tool.conda]`, and when the target `pyproject.toml` already exists it
splices the `[tool.conda]` subtree into the existing document so peer
`[project]`, `[build-system]`, `[tool.ruff]`, `[tool.pixi]`, and
friends survive untouched. Any existing `[tool.conda]` is replaced by the
flattened selected environment.
`conda.toml` and `pixi.toml` keep the default overwrite semantics of
every other conda exporter.

Manifest exporters preserve representable conda source fields and named,
credential-free PyPI direct URLs. They fail before writing when a PyPI
marker, path source, VCS source, or malformed requirement cannot cross
conda's environment exporter interface without losing meaning. Keep
those declarations in the source manifest, or replace them with a
supported named direct URL or a target-specific dependency table.

When the export `--file` is passed without `--format`, the format is
inferred from the output basename: `conda.toml` maps to `conda-toml`,
`pixi.toml` maps to `pixi-toml`, `pyproject.toml` maps to
`pyproject-toml`, `environment.yml` / `environment.yaml` maps to
`environment-yaml`, `environment.json` maps to `environment-json`, and
`conda.lock` maps to `conda-workspaces-lock-v1`.

See [Format aliases](../reference/format-aliases.md) for the full alias
table.
