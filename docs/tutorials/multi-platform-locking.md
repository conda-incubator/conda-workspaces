# Multi-platform locking

This tutorial walks through locking a workspace for multiple platforms
so that teammates on Linux, macOS, and Windows all get reproducible
environments from the same `conda.lock`.

## Prerequisites

- conda (>= 24.7) with conda-workspaces >= 0.4.0 installed
- An existing workspace with a `conda.toml` (see [Your first
  project](first-project.md) if you need one)

## Declare your platforms

If you are starting a new project, pass `--platform` (repeatable) to
`workspace init`:

```bash
conda workspace init --platform linux-64 --platform osx-arm64 --platform win-64
```

Or if you already have a `conda.toml`, edit the `platforms` list
directly:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.11"
numpy = ">=1.26"
```

If you omit `--platform` during init, only your current platform is
added. The `platforms` list tells the lock command which subdirs to
solve for.

## Add a platform to an existing workspace

There is no dedicated CLI command to add a platform after the fact.
Edit the `platforms` array in your `conda.toml` directly:

```toml
[workspace]
platforms = ["linux-64", "osx-arm64", "win-64"]  # added win-64
```

Then re-lock to generate solutions for the new platform:

```bash
conda workspace lock
```

If you only want to solve the newly added platform (faster for large
workspaces), target it explicitly:

```bash
conda workspace lock --platform win-64
```

This writes a lockfile covering just `win-64`. To fold it into your
existing `conda.lock` that already covers the other platforms, use
`--output` and `--merge`:

```bash
conda workspace lock --platform win-64 --output conda.lock.win-64
conda workspace lock --merge "conda.lock.*"
```

Or simply re-run `conda workspace lock` without flags to regenerate
the full lockfile from scratch.

## Generate the lockfile

Run `conda workspace lock` from the project root:

```bash
conda workspace lock
```

This solves every environment for every declared platform and writes a
single `conda.lock`. On a macOS ARM machine it will solve for
`linux-64` and `win-64` in addition to `osx-arm64`, using conda's
virtual package overrides to target the correct subdir.

The output looks something like:

```
Locking default for linux-64...
Locking default for osx-arm64...
Locking default for win-64...
Wrote conda.lock (3 environments × 3 platforms)
```

## Lock a subset of platforms

If you only need to refresh one platform (for example, after adding a
Linux-only dependency), pass `--platform`:

```bash
conda workspace lock --platform linux-64
```

This flag is repeatable:

```bash
conda workspace lock --platform linux-64 --platform osx-arm64
```

Unknown platforms (typos like `lnux-64`) are rejected before the
solver runs.

## Add platform-specific dependencies

Some packages only exist on certain platforms. Use `[target]` tables:

```toml
[target.linux-64.dependencies]
linux-headers = ">=5.10"

[target.osx-arm64.dependencies]
llvm-openmp = ">=14.0"
```

Re-run `conda workspace lock` and only the affected platforms pick up
the new packages.

## Pin system requirements

When cross-solving (for example, generating a `linux-64` lock from
macOS), the solver needs to know target system constraints. Use the
`[system-requirements]` table:

```toml
[system-requirements]
glibc = "2.17"
```

This adds `__glibc >=2.17` as a virtual package constraint for Linux
solves. Without it the solver may pick packages that require a newer
glibc than your deployment target.

You can also pin via environment variables for one-off overrides:

```bash
CONDA_OVERRIDE_GLIBC=2.17 conda workspace lock --platform linux-64
```

## Handle unsolvable platforms

Some combinations of dependencies are unsolvable on certain platforms
(a package might not be built for `win-64` yet). By default the lock
command fails on the first broken solve. To keep going and lock what
you can:

```bash
conda workspace lock --skip-unsolvable
```

This prints a yellow `Skipping ...` line for each failed
(environment, platform) pair and writes the lockfile with the
successful solves. If every pair fails, no lockfile is written and the
command exits with an error.

## Install from the lockfile

On any machine, install the exact locked versions without re-solving:

```bash
conda workspace install --locked
```

This validates that the lockfile is still fresh relative to the
manifest. If you've changed `conda.toml` since locking, the install
fails and asks you to re-lock. To skip that check:

```bash
conda workspace install --frozen
```

## Verify the platform set

To see which platforms are reachable (workspace-level plus any
feature-level additions):

```bash
conda workspace info
```

The text output shows a "Known Platforms" row when features expand
beyond the workspace default. The JSON form exposes this as
`known_platforms`:

```bash
conda workspace info --json | jq .known_platforms
```

## Next steps

- Split locking across CI runners with `--output` and `--merge` (see
  [CI pipeline](ci-pipeline.md#matrix-split-locking))
- Add [features](../features.md#platform-targeting) with per-feature platform lists
- Check the [Lock section](../features.md#lock) for all flags and
  error handling details
