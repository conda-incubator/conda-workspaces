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
conda workspace export -e default --file environment.yml

# environment.json, format auto-detected from the filename
conda workspace export -e default --file environment.json

# Re-emit the workspace as a conda.toml manifest (cross-format export)
conda workspace export --format conda-toml --file conda.toml

# Same content, nested under [tool.conda] in pyproject.toml
conda workspace export --format pyproject-toml --file pyproject.toml

# Multi-platform conda.lock re-emit via the lockfile exporter
conda workspace export --format conda-workspaces-lock-v1 \
    --platform linux-64 --platform osx-arm64 --file conda.lock

# Build the export from an existing conda.lock rather than re-solving
conda workspace export --from-lockfile --file environment.yml

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

`--platform` (repeatable) intersects declared or available platforms
with the chosen subset. Passing multiple platforms requires an exporter
that opts into `multiplatform_export`. The `conda-workspaces-lock-v1`,
rattler-lock-v6, and the three manifest exporters (`conda-toml`,
`pixi-toml`, `pyproject-toml`) do. The single-platform YAML and JSON
exporters raise a clear error.

## Manifest-format exporters

:::{versionadded} 0.4.0
Three new exporter plugins — `conda-toml`, `pixi-toml`, and
`pyproject-toml` — round-trip a workspace back into any manifest
dialect conda-workspaces already reads. Combined with
`conda workspace import`, this makes `conda workspace` a
bidirectional translator across every supported manifest format.
:::

The `conda-toml`, `pixi-toml`, and `pyproject-toml` exporters round-trip
a workspace back into any of the manifest dialects conda-workspaces
already reads. Declared specs that appear on every requested platform
land under the top-level `[dependencies]` / `[pypi-dependencies]`
tables. Platform-specific deltas move under `[target.<platform>.*]`.
The `pyproject-toml` exporter wraps the same content under
`[tool.conda]`, and when the target `pyproject.toml` already exists it
splices the `[tool.conda]` subtree into the existing document so peer
`[project]`, `[build-system]`, `[tool.ruff]`, `[tool.pixi]`, and
friends survive untouched. Any stale `[tool.conda]` is replaced.
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
