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
      - uses: actions/checkout@v4

      - uses: conda-incubator/setup-miniconda@v3
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

## Caching environments

Speed up CI by caching the `.conda/envs/` directory:

```yaml
      - uses: actions/cache@v4
        with:
          path: .conda/envs
          key: conda-envs-${{ runner.os }}-${{ hashFiles('conda.toml') }}
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
      - uses: actions/checkout@v4
      - uses: conda-incubator/setup-miniconda@v3
        with:
          miniforge-version: latest
      - run: conda install -c conda-forge conda-workspaces
      - run: conda workspace install -e test
      - run: conda task run -e test check

  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: conda-incubator/setup-miniconda@v3
        with:
          miniforge-version: latest
      - run: conda install -c conda-forge conda-workspaces
      - run: conda workspace install -e docs
      - run: conda task run -e docs build-docs
```

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
      - uses: actions/checkout@v4
      - uses: conda-incubator/setup-miniconda@v3
        with:
          miniforge-version: latest
          activate-environment: ""
      - run: conda install -c conda-forge conda-workspaces
      - name: Solve ${{ matrix.platform }}
        run: |
          conda workspace lock \
            --platform ${{ matrix.platform }} \
            --output conda.lock.${{ matrix.platform }}
      - uses: actions/upload-artifact@v4
        with:
          name: conda-lock-fragment-${{ matrix.platform }}
          path: conda.lock.${{ matrix.platform }}

  merge:
    needs: solve
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: conda-incubator/setup-miniconda@v3
        with:
          miniforge-version: latest
          activate-environment: ""
      - run: conda install -c conda-forge conda-workspaces
      - uses: actions/download-artifact@v4
        with:
          pattern: conda-lock-fragment-*
          merge-multiple: true
      - name: Merge fragments into conda.lock
        run: conda workspace lock --merge "conda.lock.*"
      - uses: actions/upload-artifact@v4
        with:
          name: conda-lock
          path: conda.lock
```

The coordinator never runs a solver, so it can stay on the
lightest runner available. On failure, any fragment that violates
schema or channel invariants raises `LockfileMergeError` and no
`conda.lock` is written.

## Task caching in CI

If your tasks use `inputs`/`outputs` caching, the cache directory can
be preserved between runs for faster incremental builds:

```yaml
      - uses: actions/cache@v4
        with:
          path: ~/.cache/conda-workspaces
          key: conda-workspaces-tasks-${{ hashFiles('src/**/*.py') }}
```

## Tasks without workspaces

If you use conda-workspaces only for task running (no workspace
definition), your CI setup is simpler — just install dependencies
manually and run tasks:

```yaml
      - run: conda install -c conda-forge conda-workspaces pytest ruff
      - run: conda task run check
```
