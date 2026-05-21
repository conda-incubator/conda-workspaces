# Automatic lockfile management

![auto-lockfile demo](../../demos/auto-lockfile.gif)

This tutorial walks through the automatic lockfile lifecycle: how
`conda workspace install` keeps `conda.lock` current without extra
commands, how to check lockfile status, and how to control the
behavior in different scenarios.

## Prerequisites

- conda (>= 26.3) with the conda-workspaces plugin installed
- A project directory to work in

## Step 1: Install creates the lockfile

Start with a workspace that has no lockfile:

```bash
mkdir my-project && cd my-project
conda workspace init --name my-project
conda workspace add "python>=3.12"
```

Run install:

```bash
conda workspace install
```

Because no `conda.lock` exists, install runs the solver, creates
the environment, and writes a fresh lockfile. You now have both
the environment and a lockfile in one step.

## Step 2: Check the lockfile status

Use `conda workspace info` to see whether the lockfile is current:

```bash
conda workspace info
```

The **Lockfile** row shows one of three states:

| Status | Color | Meaning |
|---|---|---|
| `up-to-date` | Green | Lockfile satisfies all manifest requirements |
| `out-of-date` | Yellow | Manifest has changed since the lockfile was written |
| `missing` | Red | No `conda.lock` file found |

For scripting, use the JSON output:

```bash
conda workspace info --json | jq '.lockfile_status'
# "up-to-date"
```

When the lockfile is out of date, the `lockfile_reason` field
explains why:

```bash
conda workspace info --json | jq '.lockfile_reason'
# "Dependency 'requests' is required by environment 'default' but not found in the lockfile for platform 'osx-arm64'"
```

## Step 3: Add a dependency and see staleness detection

Add a new dependency with `conda workspace add`. By default, `add`
writes the manifest, solves, installs, and updates the lockfile in
one step:

```bash
conda workspace add "requests>=2.28"
```

To see the staleness detection separately, use `--no-install` to
only update the manifest:

```bash
conda workspace add --no-install "flask>=3"
```

Now check the status:

```bash
conda workspace info
# Lockfile    out-of-date (Dependency 'flask' is required by ...)
```

Run install to trigger the re-solve:

```bash
conda workspace install
```

The output includes a message like:

```text
Lockfile out of date: Dependency 'flask' is required by environment
'default' but not found in the lockfile for platform 'osx-arm64'.
Re-solving environments.
```

Install detects the mismatch, re-solves, installs, and updates
`conda.lock`. Check the status again:

```bash
conda workspace info
# Lockfile    up-to-date
```

## Step 4: Subsequent installs skip the solver

Run install again without changing anything:

```bash
conda workspace install
```

This time the lockfile already satisfies the manifest, so install
uses the locked package URLs directly. No solver runs, no network
metadata fetch. This is the fast path for day-to-day use.

## Step 5: Use `--locked` for strict mode

The `--locked` flag requires a satisfiable lockfile and fails
otherwise:

```bash
# This works when the lockfile is current
conda workspace install --locked

# Add a new dep without solving to make the lockfile stale
conda workspace add --no-install "httpx>=0.27"

# This fails with LockfileStaleError
conda workspace install --locked
```

The error message tells you what to do:

```text
LockfileStaleError: Lockfile 'conda.lock' does not satisfy manifest 'conda.toml'.
(Dependency 'httpx' is required by environment 'default' but not found
in the lockfile for platform 'osx-arm64')
Run 'conda workspace lock' to update it, or use --frozen to install anyway.
```

Fix it by updating the lockfile first:

```bash
conda workspace lock
conda workspace install --locked
```

## Step 6: Use `--frozen` to skip all checks

The `--frozen` flag installs whatever is in the lockfile without
checking whether it satisfies the manifest. Use this when you know
the lockfile is intentionally pinned:

```bash
conda workspace install --frozen
```

## Step 7: Force a re-solve with `--no-lock`

To pick up channel updates (new package versions) without editing
the manifest, use `--no-lock` to bypass the lockfile entirely:

```bash
conda workspace install --no-lock
```

This runs the solver even when the lockfile is satisfiable. The
lockfile is regenerated afterward.

## What triggers a re-solve?

The staleness check compares the manifest against the lockfile
contents, not file timestamps. Specifically it checks:

1. **Lockfile version** matches the expected schema version
2. **Environments** declared in the manifest all exist in the lockfile
3. **Channels** for each environment match between manifest and lockfile
4. **Platforms** declared in the manifest are covered in the lockfile
5. **Dependencies** for each environment on the current platform are
   present in the lockfile with a version that satisfies the manifest
   spec

Changes that do **not** trigger a re-solve:

- Editing comments or whitespace in the manifest
- Reordering dependencies (same set, different order)
- Changing task definitions, archive config, or activation scripts
- Adding a platform that is not the current one (checked at lock time,
  not install time)

## Summary

| Flag | Lockfile satisfiable | Lockfile stale | Lockfile missing |
|---|---|---|---|
| *(default)* | Install from lockfile | Re-solve + update | Solve + create |
| `--locked` | Install from lockfile | Error | Error |
| `--frozen` | Install from lockfile | Install from lockfile | Error |
| `--no-lock` | Full solve | Full solve | Full solve |

## Next steps

- [CI pipeline](ci-pipeline.md) for CI-specific lockfile behavior
- [Configuration reference](../configuration.md) for workspace settings
- [Features overview](../features.md) for the full feature summary
