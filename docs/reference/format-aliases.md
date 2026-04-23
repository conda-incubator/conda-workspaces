# Plugin format names and aliases

Every conda-workspaces plugin registers itself under one canonical
`FORMAT` string plus zero or more convenience `ALIASES`.  Users can
pass either the canonical name or an alias wherever conda accepts a
format identifier (`conda env create --format=...`, `conda export
--format=...`).

## Naming policy

conda-workspaces follows the naming policy that `conda-lockfiles`
documents in [its own format-aliases
reference](https://github.com/conda-incubator/conda-lockfiles/blob/main/docs/format-aliases.md):

- The canonical `FORMAT` is **versioned** when the format has an
  on-disk schema version byte (for example
  `conda-workspaces-lock-v1`).  Versioned canonical names are stable
  across future schema bumps — a future `v2` gets a sibling
  `conda-workspaces-lock-v2` rather than replacing `v1` in place.

- **Aliases** are unversioned shortcuts (for example
  `conda-workspaces-lock`, `workspace-lock`).  They resolve to the
  current canonical name today and may migrate to a newer version
  later with a deprecation notice.

- Manifests that have no on-disk schema version of their own
  (`conda.toml`) keep an unversioned canonical name
  (`conda-workspaces`) and no aliases.

## Registered names

| File          | Canonical `FORMAT`            | Aliases                                     |
| ------------- | ----------------------------- | ------------------------------------------- |
| `conda.toml`  | `conda-workspaces`            | —                                           |
| `conda.lock`  | `conda-workspaces-lock-v1`    | `conda-workspaces-lock`, `workspace-lock`   |

The single source of truth for these constants is
`conda_workspaces/env_spec.py` (manifest) and
`conda_workspaces/lockfile.py` (lockfile); `plugin.py` and
`export.py` import from there so the three code paths never drift.
