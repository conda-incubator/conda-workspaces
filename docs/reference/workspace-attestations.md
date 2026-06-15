# Workspace attestation reference

This page describes the Sigstore bundle written by
`conda workspace attest` and verified by `conda workspace verify`.
`conda workspace lock --sign` is the compact form that solves the
lockfile and immediately writes the same attestation.

Workspace attestations are sidecar JSON Sigstore bundles. The bundle
contains a DSSE-signed [in-toto Statement v1][in-toto-statement] payload
that binds the workspace manifest and `conda.lock` to the signing
identity.

[in-toto-statement]: https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md

## Status

| Field | Value |
|---|---|
| Format | Workspace attestation |
| Format version | `1` |
| JSON schema | `https://conda-incubator.github.io/conda-workspaces/workspace-attestation-1.schema.json` |
| Source schema | [`schema/workspace-attestation-1.schema.json`](https://github.com/conda-incubator/conda-workspaces/blob/main/schema/workspace-attestation-1.schema.json) |
| Statement type | `https://in-toto.io/Statement/v1` |
| Predicate type | `https://conda-incubator.github.io/conda-workspaces/workspace-attestation-1.schema.json` |
| Producers | `conda workspace attest`, `conda workspace lock --sign` |
| Consumers | `conda workspace verify`, `conda workspace install --locked --verify`, `conda workspace unarchive --verify` |

## File naming

By default, conda-workspaces writes the Sigstore bundle next to the
lockfile:

```text
conda.lock
conda.lock.sigstore.json
```

Pass `--attestation PATH` to `conda workspace attest`,
`conda workspace lock --sign`, `conda workspace verify`, or
`conda workspace install --verify` when the bundle is stored somewhere
else.

## Lifecycle

The explicit lifecycle is:

```bash
conda workspace lock
conda workspace attest
conda workspace verify \
  --cert-identity user@example.com \
  --cert-oidc-issuer https://issuer.example
```

Use `conda workspace lock --sign` when solving and signing should happen
in one step. Use `conda workspace install --locked --verify` when
verification should gate installation from an existing lockfile.

## Statement structure

The DSSE payload is a JSON object with the in-toto Statement fields
`_type`, `subject`, `predicateType`, and `predicate`.

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "conda.toml",
      "digest": {
        "sha256": "<manifest sha256>"
      }
    },
    {
      "name": "conda.lock",
      "digest": {
        "sha256": "<lockfile sha256>"
      }
    }
  ],
  "predicateType": "https://conda-incubator.github.io/conda-workspaces/workspace-attestation-1.schema.json",
  "predicate": {
    "version": 1,
    "workspace": {
      "manifest": "conda.toml",
      "lockfile": "conda.lock"
    },
    "manifest": {
      "format": "conda-toml"
    },
    "lockfile": {
      "format": "conda-workspaces-lock-v1"
    }
  }
}
```

The Sigstore bundle wraps this payload, signature, certificate, and
verification material in one JSON sidecar.

## Verification

`conda workspace verify` and `conda workspace install --locked --verify`
verify in this order:

1. Load the Sigstore bundle from `--attestation` or
   `conda.lock.sigstore.json`.
2. Verify the DSSE bundle against the expected certificate identity and
   OIDC issuer passed with `--cert-identity` and `--cert-oidc-issuer`.
3. Require the DSSE payload type to be `application/vnd.in-toto+json`.
4. Validate the in-toto Statement type, predicate type, and format
   version.
5. Verify the current manifest and `conda.lock` SHA-256 digests against
   the Statement subjects.
6. For `install --locked --verify`, continue with locked installation.

`conda workspace unarchive --verify` verifies the bundled
`conda.lock.sigstore.json` inside a temporary staging directory before
moving the extracted workspace into place. When an archive also includes
bundled package files, conda-workspaces verifies package hashes against
the attested lockfile before priming the package cache.

Verification fails closed when no trusted identity is configured. The
identity policy is intentionally supplied by the verifier through CLI
flags; conda-workspaces does not trust a downloaded manifest to declare
which signer should be accepted for that same manifest.

## Relationship to receipts

Workspace attestations and archive receipts solve different problems.

- A workspace attestation proves that a trusted Sigstore identity signed
  a specific manifest and lockfile.
- An archive receipt binds exact archive bytes, extracted workspace
  files, and lockfile package inventory, but does not prove who created
  the receipt.

Use `attest` / `verify` when signer provenance matters. Use
`lock --sign` when solving and signing should be a single command. Use
`--receipt` when you need an external integrity record for exact archive
bytes.
