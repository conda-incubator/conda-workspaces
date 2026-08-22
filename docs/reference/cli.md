# CLI Reference

## conda workspace

`conda ws` is a shorthand alias for `conda workspace`. Both forms
accept the same subcommands, flags, and arguments.

### Structured output

Successful commands that advertise `--json` emit exactly one JSON
value on stdout. Successful `add`, `update`, `remove`, `install`,
`lock`, `clean`, `import`, `archive`, and `unarchive` operations
return:

```json
{"success": true}
```

Query and export commands return command-specific data. `quickstart`
returns its workspace, environment, manifest, added specs, and shell
state. `init`, `activate`, `run`, and `shell` tolerate a global
`--json` flag but intentionally emit no structured result.

`attest --json` returns `success` and the `sidecar` path. `verify --json`
returns the checked sidecar, manifest, lockfile, predicate type, authenticated
signer, and timestamps. `verified` is `true` after successful verification.
`authorized` is `true` when a supplied signer policy matches and `null` when no
policy was supplied. See
[Workspace attestation reference](workspace-attestations.md) for the exact
objects.

`conda workspace sbom --json` returns `success`, `format`, and `environment`.
It adds `content` when writing to stdout or previewing a file with `--dry-run`.
After a file is written, it returns the path in `file` instead:

```json
{
  "success": true,
  "format": "cyclonedx-json-v1.7",
  "environment": "default",
  "file": "exports/default.cdx.json"
}
```

### Workspace snapshot

`conda workspace info --json` returns the workspace metadata and lock
status together with `environment_details`. Each environment entry
includes its declared features, `no_default_feature`, prefix,
installation state, channels, supported platforms, and one resolution
for every platform. Resolved conda and PyPI dependencies include the
winning manifest table:

```json
{
  "spec": "python >=3.12 py*",
  "provenance": {
    "table": "[environments.test.dependencies]",
    "location": {
      "environment": "test",
      "feature": null,
      "platform": null
    },
    "inherited_from": "[workspace.dependencies]"
  }
}
```

Use `provenance.location` to build the `--environment`, `--feature`, and
`--platform` selectors accepted by `workspace remove` and, for conda
dependencies, `workspace update`. PyPI removals also need `--pypi`, identified
by the containing `pypi_dependencies` collection. `environment` and `feature`
are mutually exclusive, and all three fields are `null` for a top-level default
declaration. `platform` is the manifest target key accepted by `--platform`,
including a rich workspace platform name rather than only its resolved conda
subdir. `table` remains the human-readable manifest location and should not be
parsed as a machine contract.

`inherited_from` appears only when the winning conda declaration uses
`{ workspace = true }`. In that case, `location` still identifies the
declaration selected for mutation. Rich platform entries report both the
declared platform name and its conda subdir. PyPI specs use their
manifest-compatible structured form so Git selectors and editable paths remain
available to consumers.

Pass `--packages` to include each installed prefix's package records:

```bash
conda workspace info --json --packages
```

Without `--json`, `--packages` appends package tables to the workspace
overview.

This single call replaces separate `workspace envs`,
`workspace info -e`, and `workspace list -e` calls for every
environment. Without `--packages`, package records are omitted.
Uninstalled environments have an empty `packages` list when package
records are requested.

Auto-generated from the `conda workspace` argument parser.

```{eval-rst}
.. argparse::
   :module: conda_workspaces.cli
   :func: generate_workspace_parser
   :prog: conda workspace
```

## conda task

Auto-generated from the `conda task` argument parser.

```{eval-rst}
.. argparse::
   :module: conda_workspaces.cli
   :func: generate_task_parser
   :prog: conda task
```
