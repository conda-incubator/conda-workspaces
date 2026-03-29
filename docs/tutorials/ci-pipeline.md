# CI pipeline

This tutorial shows how to use conda-workspaces in GitHub Actions to
install and test your project.

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
        run: cw install -e test

      - name: Run tests
        run: cw run -e test pytest -v --tb=short
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
        run: cw install -e test
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
      - run: cw install -e test
      - run: cw run -e test pytest -v

  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: conda-incubator/setup-miniconda@v3
        with:
          miniforge-version: latest
      - run: conda install -c conda-forge conda-workspaces
      - run: cw install -e docs
      - run: cw run -e docs sphinx-build docs docs/_build/html
```

## With conda-tasks

If your project also uses [conda-tasks](https://github.com/conda-incubator/conda-tasks)
for task definitions, you can combine both plugins:

```yaml
      - run: conda install -c conda-forge conda-workspaces conda-tasks
      - run: cw install -e test
      - run: cw run -e test conda task run check
```
