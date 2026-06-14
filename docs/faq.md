# FAQ

## How does conda-workspaces handle manifest format changes in pixi?

conda-workspaces tracks the pixi manifest format and maintains
compatibility. The format is also being standardized through a Conda
Enhancement Proposal (CEP); see the [CEP tracker
issue](https://github.com/conda-incubator/conda-workspaces/issues/52)
for progress.

The [`conda.toml` specification](reference/conda-toml-spec.md) is the
normative prose reference, and
[`schema/conda-toml-1.schema.json`][schema] is the machine-readable
schema. Any pixi-originated fields that conda-workspaces accepts but
does not implement (like `solve-group`) are documented explicitly in the
spec.

[schema]: https://github.com/conda-incubator/conda-workspaces/blob/main/schema/conda-toml-1.schema.json

## How does conda-workspaces differ from conda-project and anaconda-project?

See the [Motivation](motivation.md) page for a detailed comparison. The
key differences:

1. conda-workspaces uses pixi's TOML manifest format rather than
   inventing a new one, so projects can share manifests between tools.
2. It integrates as a conda plugin rather than a standalone CLI.
3. It includes `conda workspace import` commands to convert from both
   `conda-project.yml` and `anaconda-project.yml`.

## What happens if I add keys to conda.toml that conda-workspaces doesn't recognize?

The runtime parser is permissive when reading manifests and ignores
unknown keys in most tables. Commands that edit TOML with `tomlkit`,
such as `conda workspace add` and `conda task add`, generally preserve
unrelated tables and comments because they modify the existing document
in place.

The JSON schema is stricter: it rejects unknown keys so `conda.toml` has
a stable validation target for the fields conda-workspaces standardizes
today. Put pixi-only metadata in `pixi.toml` or `[tool.pixi.*]` when you
need pixi's full manifest surface.
