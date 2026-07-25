# CLI Reference

## conda workspace

`conda ws` is a shorthand alias for `conda workspace`. Both forms
accept the same subcommands, flags, and arguments.

### Structured output

Successful commands that advertise `--json` emit exactly one JSON
value on stdout. Successful `add`, `remove`, `install`, `lock`, `clean`,
`import`, `archive`, and `unarchive` operations return:

```json
{"success": true}
```

Query and export commands return command-specific data. `quickstart`
returns its workspace, environment, manifest, added specs, and shell
state. `init`, `activate`, `run`, and `shell` tolerate a global
`--json` flag but intentionally emit no structured result.

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
