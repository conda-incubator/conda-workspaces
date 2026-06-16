# CI pipeline

This tutorial shows how to use conda-workspaces in GitHub Actions to
install environments, run tasks, and test your project.

## Basic setup

```yaml
# .github/workflows/test.yml
name: Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]

    steps:
      - uses: actions/checkout@v6

      - uses: conda-incubator/setup-miniconda@v4
        with:
          miniforge-version: latest
          activate-environment: ""

      - name: Install conda-workspaces
        run: conda install -c conda-forge conda-workspaces

      - name: Install test environment
        run: conda workspace install -e test

      - name: Run tests
        run: conda task run -e test check
```

:::{tip}
When `CI=true` is set (as it is by default in GitHub Actions, GitLab
CI, and most CI systems), `conda workspace install` automatically
behaves like `--locked`. It installs from the lockfile and fails if
it does not satisfy the manifest. No extra flags needed.
:::

### What happens when the lockfile is stale in CI?

If someone updates `conda.toml` without running `conda workspace lock`,
the CI job fails with a clear error:

```text
LockfileStaleError: Lockfile 'conda.lock' does not satisfy manifest 'conda.toml'.
(Dependency 'requests' is required by environment 'default' but not found
in the lockfile for platform 'linux-64')
Run 'conda workspace lock' to update it, or use --frozen to install anyway.
```

The developer fixes this locally by running `conda workspace lock` (or
just `conda workspace install`, which updates the lockfile
automatically) and committing the updated `conda.lock`.

## Caching environments

Speed up CI by caching the `.conda/envs/` directory:

```yaml
      - uses: actions/cache@v5
        with:
          path: .conda/envs
          key: conda-envs-${{ runner.os }}-${{ hashFiles('conda.lock') }}
          restore-keys: |
            conda-envs-${{ runner.os }}-

      - name: Install test environment
        run: conda workspace install -e test
```

## Multiple environments

Run different checks in separate jobs:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: conda-incubator/setup-miniconda@v4
        with:
          miniforge-version: latest
      - run: conda install -c conda-forge conda-workspaces
      - run: conda workspace install -e test
      - run: conda task run -e test check

  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: conda-incubator/setup-miniconda@v4
        with:
          miniforge-version: latest
      - run: conda install -c conda-forge conda-workspaces
      - run: conda workspace install -e docs
      - run: conda task run -e docs build-docs
```

(matrix-split-locking)=

## Matrix-split locking

![ci-split demo](../../demos/ci-split.gif)

:::{versionadded} 0.4.0
Requires `--output` and `--merge`, both introduced in 0.4.0.
:::

`conda workspace lock` can split solving across a matrix and stitch
the per-platform fragments back into a single `conda.lock` on a
coordinator job. This keeps lock refreshes fast as the platform
list grows, and each runner only has to install the solver bits for
the platforms it owns.

`--output <path>` writes the solved lockfile to an arbitrary
location so each matrix runner emits exactly one fragment;
`--merge <glob>` (repeatable) combines fragments without running
the solver. The merger validates schema-version agreement, each
environment's channel list, and rejects overlapping `(environment,
platform)` pairs — the resulting `conda.lock` is byte-stable with
what a single-run `conda workspace lock` would produce. `--merge`
is mutually exclusive with `--environment`, `--platform`,
`--skip-unsolvable`, and `--output`.

```yaml
# .github/workflows/lock.yml
name: Refresh conda.lock

on:
  workflow_dispatch:
  schedule:
    - cron: "0 6 * * 1"   # Mondays, 06:00 UTC

jobs:
  solve:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: ubuntu-latest
            platform: linux-64
          - os: macos-latest
            platform: osx-arm64
          - os: windows-latest
            platform: win-64
    steps:
      - uses: actions/checkout@v6
      - uses: conda-incubator/setup-miniconda@v4
        with:
          miniforge-version: latest
          activate-environment: ""
      - run: conda install -c conda-forge conda-workspaces
      - name: Solve ${{ matrix.platform }}
        run: |
          conda workspace lock \
            --platform ${{ matrix.platform }} \
            --output conda.lock.${{ matrix.platform }}
      - uses: actions/upload-artifact@v7
        with:
          name: conda-lock-fragment-${{ matrix.platform }}
          path: conda.lock.${{ matrix.platform }}

  merge:
    needs: solve
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: conda-incubator/setup-miniconda@v4
        with:
          miniforge-version: latest
          activate-environment: ""
      - run: conda install -c conda-forge conda-workspaces
      - uses: actions/download-artifact@v8
        with:
          pattern: conda-lock-fragment-*
          merge-multiple: true
      - name: Merge fragments into conda.lock
        run: conda workspace lock --merge "conda.lock.*"
      - uses: actions/upload-artifact@v7
        with:
          name: conda-lock
          path: conda.lock
```

The coordinator never runs a solver, so it can stay on the
lightest runner available. On failure, any fragment that violates
schema or channel invariants raises `LockfileMergeError` and no
`conda.lock` is written.

## Nightly lockfile refresh

Set up a scheduled workflow that re-solves the lockfile and opens a
pull request when package versions change. Use `--no-lock` to bypass
the CI-default strict mode and force a fresh solve:

```yaml
# .github/workflows/refresh-lock.yml
name: Refresh conda.lock

on:
  schedule:
    - cron: "0 6 * * 1"   # Mondays, 06:00 UTC
  workflow_dispatch:

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: conda-incubator/setup-miniconda@v4
        with:
          miniforge-version: latest
          activate-environment: ""
      - run: conda install -c conda-forge conda-workspaces

      - name: Re-solve and update lockfile
        run: conda workspace lock

      - name: Open PR if lockfile changed
        uses: peter-evans/create-pull-request@v8
        with:
          commit-message: "Update conda.lock"
          title: "Update conda.lock"
          branch: auto/conda-lock-refresh
          delete-branch: true
```

This keeps your lockfile fresh with upstream releases while
preserving the safety of locked installs on every other CI run.

## Signed lockfile refresh

If a refreshed lockfile is published for other jobs or systems to
consume, sign it in the same workflow that creates it. GitHub Actions
needs `id-token: write` so Sigstore can use the workflow identity:

```yaml
permissions:
  contents: read
  id-token: write
```

Install Sigstore alongside conda-workspaces and sign the result:

```yaml
      - run: conda install -c conda-forge conda-workspaces sigstore

      - name: Re-solve and sign lockfile
        run: conda workspace lock --sign
```

Downstream jobs should verify against the exact workflow identity and
issuer they trust:

```bash
conda workspace install --locked --verify \
  --cert-identity "https://github.com/ORG/REPO/.github/workflows/refresh-lock.yml@refs/heads/main" \
  --cert-oidc-issuer "https://token.actions.githubusercontent.com"
```

See [Sign and verify workspace lockfiles](../how-to/workspace-attestations.md)
for the full attestation workflow.

## Task caching in CI

If your tasks use `inputs`/`outputs` caching, the cache directory can
be preserved between runs for faster incremental builds:

```yaml
      - uses: actions/cache@v5
        with:
          path: ~/.cache/conda-workspaces
          key: conda-workspaces-tasks-${{ hashFiles('src/**/*.py') }}
```

## FAQ

### Which CI systems set `CI=true` automatically?

GitHub Actions, GitLab CI, Travis CI, CircleCI, Azure Pipelines,
Buildkite, Bitbucket Pipelines, and most other hosted CI systems set
`CI=true` by default. If your CI system does not, set it yourself:

```yaml
env:
  CI: "true"
```

### How do I override the CI default and allow re-solving?

Pass `--no-lock` to bypass the lockfile entirely and force a fresh
solve, even when `CI=true`:

```bash
conda workspace install --no-lock
```

This is useful for nightly jobs that pick up new upstream releases
(see *Nightly lockfile refresh* above).

### When should I use `--locked` vs `--frozen`?

| Flag | Use when |
| --- | --- |
| *(default in CI)* | Normal CI runs. Equivalent to `--locked`. |
| `--locked` | You want a clear error if the lockfile is stale. This is the CI default. |
| `--frozen` | You intentionally pinned older versions and do not want staleness checks. Installs whatever is in `conda.lock` without validating against the manifest. |

### What causes a lockfile merge to fail?

`conda workspace lock --merge` rejects fragments when:

- Fragments use different schema versions.
- The channel list for a shared environment differs between fragments.
- Two fragments contain the same `(environment, platform)` pair.

The error message names the conflicting fragment. Fix the input
fragments and re-run the merge.

### How do I handle platforms that fail to solve?

Use `--skip-unsolvable` to let the solver continue past platforms
where no solution exists:

```bash
conda workspace lock --skip-unsolvable
```

Skipped platforms are reported as warnings. The resulting lockfile
covers only the platforms that solved successfully. CI jobs for
skipped platforms will fail at install time with a clear
`LockfileNotFoundError`.

### How do I manage disk space on long-lived CI runners?

conda-workspaces stores environments under `.conda/envs/` relative
to the workspace root. On long-lived runners where old environments
accumulate:

```bash
# Remove all workspace environments
rm -rf .conda/envs/

# Or remove conda's package cache
conda clean --all -y
```

When using GitHub Actions cache, the `key` tied to `conda.lock`
means stale caches are automatically evicted when the lockfile
changes.

## Tasks without workspaces

If you use conda-workspaces only for task running (no workspace
definition), your CI setup is simpler — just install dependencies
manually and run tasks:

```yaml
      - run: conda install -c conda-forge conda-workspaces pytest ruff
      - run: conda task run check
```
