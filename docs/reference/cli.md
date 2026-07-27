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
    "inherited_from": "[workspace.dependencies]"
  }
}
```

`inherited_from` appears only when the winning conda declaration uses
`{ workspace = true }`. Rich platform entries report both the declared
platform name and its conda subdir. PyPI specs use their
manifest-compatible structured form so Git selectors and editable
paths remain available to consumers.

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
