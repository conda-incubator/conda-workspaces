# Workspace attestation reference

`conda workspace attest` and `conda workspace lock --sign` write a Sigstore
Bundle v0.3 JSON object containing a DSSE-signed in-toto Statement v1.
conda-workspaces defines the workspace predicate and delegates OIDC, signing,
trust material, and cryptographic verification to conda-sigstore.

| Field | Value |
|---|---|
| Statement type | `https://in-toto.io/Statement/v1` |
| DSSE payload type | `application/vnd.in-toto+json` |
| Predicate type | `https://conda-incubator.github.io/conda-workspaces/workspace-attestation-1.schema.json` |
| Lockfile format | `conda-workspaces-lock-v1` |
| Default sidecar | `conda.lock.sigstore.json` |
| Default maximum sidecar size | 10 MiB |

The source schema is
[`schema/workspace-attestation-1.schema.json`](https://github.com/conda-incubator/conda-workspaces/blob/main/schema/workspace-attestation-1.schema.json).

## Statement

The statement contains exactly two subjects. Their names are the selected root
manifest and canonical `conda.lock`.

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "conda.toml",
      "digest": {"sha256": "<manifest sha256>"}
    },
    {
      "name": "conda.lock",
      "digest": {"sha256": "<lockfile sha256>"}
    }
  ],
  "predicateType": "https://conda-incubator.github.io/conda-workspaces/workspace-attestation-1.schema.json",
  "predicate": {
    "version": 1,
    "workspace": {
      "manifest": "conda.toml",
      "lockfile": "conda.lock"
    },
    "manifest": {"format": "conda-toml"},
    "lockfile": {"format": "conda-workspaces-lock-v1"}
  }
}
```

The manifest format must match the selected parser. Supported values are
`conda-toml`, `pixi-toml`, and `pyproject-toml`. Subject paths must be safe
root-relative POSIX paths, and SHA-256 values use lowercase hexadecimal.

## Signing contract

`conda workspace attest` signs the current files under the workspace publication
guard:

1. Capture the bounded manifest and lockfile bytes and their file generations.
2. Build and sign the statement in memory.
3. Revalidate both input generations.
4. Publish the sidecar as a guarded file generation without following links.

The sidecar cannot be a symbolic link, be reached through a symbolic-link
parent, or alias the manifest or lockfile directly or through a hardlink. If an
existing sidecar changes during signing, the command preserves the replacement
and fails. A failed or dry-run command does not publish a new sidecar.

On Windows, replacing an existing sidecar uses two exclusive renames because
descriptor-relative name exchange is unavailable. If the process is interrupted
between those operations, the destination can be absent while the prior
generation remains in a high-entropy `.rollback` entry beside it. Recoverable
publication errors report that path, and a concurrent claimant is never
overwritten.

`lock --sign` signs the rendered canonical lockfile bytes before they reach
disk. It then publishes the canonical lockfile and sidecar under the same
guard. If sidecar publication fails, the command restores the previous
lockfile only when the live lockfile is still the generation it published. A
concurrent replacement is preserved and the command fails.

`lock --sign` accepts the canonical output and the canonical result of
`--merge`. It rejects noncanonical `--output` paths that would create a signed
fragment with no matching verification interface.

## Verification contract

Verification performs these checks before reporting success:

1. Read the sidecar as a stable regular file under the configured size limit,
   which defaults to 10 MiB.
2. Verify the Bundle v0.3 structure, DSSE signature, certificate, and
   transparency material through conda-sigstore, then report supported
   timestamp evidence from the verified bundle.
3. Require the workspace predicate type and format version.
4. Require exactly the selected manifest and canonical lockfile subjects.
5. Compare their digests with the exact bounded file bytes.
6. If the receiver supplied a certificate identity and OIDC issuer, require
   the authenticated signer to equal that pair.

Cryptographic validity and signer authorization are separate checks. The
generic conda-sigstore verifier reports the authenticated signer. The workspace
command applies the receiver's exact signer rule afterward. Without that rule,
verification reports `authorized` as `null`. `install --verify` and
`unarchive --verify` require an explicit matching signer before they mutate
external state.

For `install --verify`, the verified lockfile byte buffer is the buffer parsed
for installation.

## JSON output

`conda workspace attest --json` returns:

```json
{
  "success": true,
  "sidecar": "/path/to/conda.lock.sigstore.json"
}
```

`conda workspace verify --json` with a matching signer policy returns:

```json
{
  "success": true,
  "verified": true,
  "authorized": true,
  "sidecar": "/path/to/conda.lock.sigstore.json",
  "manifest": "/path/to/conda.toml",
  "lockfile": "/path/to/conda.lock",
  "predicate_type": "https://conda-incubator.github.io/conda-workspaces/workspace-attestation-1.schema.json",
  "signer": {
    "identity": "https://github.com/ORG/REPO/.github/workflows/release.yml@refs/heads/main",
    "issuer": "https://token.actions.githubusercontent.com",
    "timestamps": []
  }
}
```

Without a signer policy, `authorized` is `null`.

Failed verification exits nonzero and does not emit a successful result.

## Security boundaries

A valid bundle and a matching signer do not show that the lockfile is the
newest release. Sigstore timestamps authenticate signing-time evidence but do
not provide downgrade protection. Select the expected release through a trusted
repository, release manifest, or deployment policy.

Workspace attestations do not sign archive bytes or individual conda packages.
`conda workspace archive --sign` signs an
[archive receipt](archive-receipts.md), which binds the exact archive digest
and inventory. Conda-sigstore's per-package verifier is separate and disabled
by default. It runs only when conda provides its package-verifier hook and
`plugins.conda_sigstore_enforce` is enabled. Current released conda versions do
not provide that hook. The package verifier checks CEP 27 evidence but does not
authorize package signers.
