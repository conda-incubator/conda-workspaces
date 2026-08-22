# Sign and verify a workspace

Use a workspace attestation when another job or person must check who signed
the exact manifest and `conda.lock` they are about to use.

## Install attestation support

The Sigstore integration is optional. Install conda-sigstore 0.1.0 or newer in
the environment where conda and conda-workspaces are installed:

```bash
conda install -c conda-forge conda-workspaces "conda-sigstore>=0.1.0"
```

For a pip installation, use the optional extra:

```bash
python -m pip install "conda-workspaces[attestations]"
```

## Sign the manifest and canonical lockfile

Solve and sign in one command:

```bash
conda workspace lock --sign
```

This writes the canonical lockfile and its sidecar:

```text
conda.lock
conda.lock.sigstore.json
```

If you already have the lockfile you want to sign, sign it without solving:

```bash
conda workspace attest
```

Use `--attestation` to choose another sidecar path:

```bash
conda workspace attest --attestation dist/conda.lock.sigstore.json
```

Signing discovers an ambient OIDC credential. In a local shell, the Sigstore
client may open a browser. In CI, grant the job an OIDC token through the CI
provider instead of putting a bearer token in the command line.

`lock --sign` signs only the canonical `conda.lock`. It rejects a noncanonical
`--output`, because `workspace verify` and verified installation consume the
canonical file. Matrix jobs can merge their fragments and sign the result:

```bash
conda workspace lock --merge "conda.lock.*" --sign
```

Use `--dry-run` to validate paths and option combinations without requesting an
OIDC credential or writing a sidecar.

## Verify the current workspace

Set the exact signer identity and issuer accepted by your release policy:

```bash
export SIGSTORE_CERT_IDENTITY="https://github.com/ORG/REPO/.github/workflows/release.yml@refs/heads/main"
export SIGSTORE_OIDC_ISSUER="https://token.actions.githubusercontent.com"
```

Then verify the workspace:

```bash
conda workspace verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Use `--attestation` when the sidecar is stored elsewhere. The identity and
issuer flags must be passed together. Without them, the command authenticates
and reports the signer but does not decide whether that signer is authorized.

For scripts, request one JSON result:

```bash
conda workspace verify --json \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

The result includes the authenticated identity, issuer, Sigstore timestamps,
predicate type, and the sidecar, manifest, and lockfile paths. `authorized` is
`null` when no signer policy was supplied.

## Require verification before installation

Use `--locked --verify` to require a current lockfile and a matching
attestation:

```bash
conda workspace install --locked --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Use `--frozen --verify` only when the manifest-to-lockfile satisfiability check
must be skipped. The attestation must still match both files.

## Sign an archive receipt

Create an archive and sign its receipt with Sigstore:

```bash
conda workspace archive --lock --sign \
  -o dist/my-project.tar.zst
```

This writes the archive and `dist/my-project.tar.zst.sigstore.json`. The bundle
contains the signed receipt, so a separate receipt file is not required for
verification.

Require the expected signer before extraction:

```bash
conda workspace unarchive dist/my-project.tar.zst \
  --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Add `--receipt` when you also want an unsigned JSON copy of the receipt. When
that copy is supplied to `unarchive --verify`, it must match the signed payload.
Use `--attestation PATH` on both commands when the Sigstore sidecar is stored
somewhere else.

The signed receipt binds the archive digest, manifest, lockfile, and package
inventory to the authenticated signer. The target directory is published only
after the signature, receipt, and staged files pass their checks.

Use `workspace attest` for a live workspace and `archive --sign` for an archive
handoff.
