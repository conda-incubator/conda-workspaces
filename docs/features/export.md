# Export and format interoperability

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

## CycloneDX SBOMs with conda-sboms

[`conda-sboms`](https://github.com/jezdez/conda-sboms) is a separate
exporter plugin. Install it in the environment that owns the `conda`
executable, alongside conda-workspaces. conda-workspaces discovers its
formats through the existing exporter hook. It does not contain a CycloneDX
writer or depend on conda-sboms.

Export one environment and platform from an existing `conda.lock`:

```console
conda workspace export \
  --environment default \
  --from-lockfile \
  --platform linux-64 \
  --format cyclonedx-json-v1.7 \
  --file exports/default-linux-64.cdx.json
```

The lockfile supplies exact conda package records, but the current conversion
does not preserve authoritative top-level requirements from the manifest.
conda-sboms therefore connects the environment root to inferred graph roots.
The exporter accepts one platform at a time. A selected lockfile environment
containing pip or other external packages is currently rejected before the
exporter runs.

Use an installed prefix when conda history should supply the requested roots:

```console
conda workspace export \
  --environment default \
  --from-prefix \
  --from-history \
  --format cyclonedx-json-v1.7 \
  --file exports/default.cdx.json
```

The output covers the resolved conda package graph supplied to the exporter.
It does not establish complete product coverage or Cyber Resilience Act
conformity. See the
[conda-sboms workspace guide](https://jezdez.github.io/conda-sboms/how-to/conda-workspaces/)
for the format's exact behavior and coverage limits. [Issue
#159](https://github.com/conda-incubator/conda-workspaces/issues/159) tracks
preserving workspace dependency intent when exporting resolved lock records.
