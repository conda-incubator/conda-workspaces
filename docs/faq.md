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

Unknown keys are silently preserved and do not produce warnings. This is
intentional: it allows other tools or future extensions to use the same
file without causing noise. The `[workspace]` table already supports
optional fields like `description` and `version` that not every workflow
uses.
