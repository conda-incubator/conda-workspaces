# Sign and verify workspace lockfiles

Use these recipes when you already have a workspace and want to add
Sigstore provenance to its manifest and `conda.lock`.

## Install signing support

The conda package keeps Sigstore optional. Install it next to
conda-workspaces before signing or verifying:

```bash
conda install -c conda-forge conda-workspaces sigstore
```

For pip installs:

```bash
python -m pip install "conda-workspaces[signing]"
```

## Sign the current workspace

Solve and sign in one command:

```bash
conda workspace lock --sign
```

This writes `conda.lock.sigstore.json` next to `conda.lock`.

If the lockfile is already final, sign without solving:

```bash
conda workspace attest
```

Store the Sigstore bundle somewhere else with `--attestation`:

```bash
conda workspace attest --attestation dist/conda.lock.sigstore.json
```

Pass `--identity-token` only when your CI provider gives you an OIDC
token explicitly:

```bash
conda workspace attest --identity-token "$OIDC_TOKEN"
```

## Verify the current workspace

Verification needs an explicit trust policy:

```bash
conda workspace verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Use `--attestation` when the bundle is not next to `conda.lock`:

```bash
conda workspace verify \
  --attestation dist/conda.lock.sigstore.json \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

For scripts, add `--json`:

```bash
conda workspace verify --json \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

## Verify before installation

Use `--locked --verify` for normal reproducible installs. This rejects a
missing or stale lockfile before verifying the Sigstore bundle:

```bash
conda workspace install --locked --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Use `--frozen --verify` only when freshness checks are intentionally
disabled:

```bash
conda workspace install --frozen --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

## Sign in GitHub Actions

Grant the workflow an OIDC token and install Sigstore:

```yaml
permissions:
  contents: read
  id-token: write

jobs:
  lock:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: conda-incubator/setup-miniconda@v3
        with:
          miniforge-version: latest
          activate-environment: ""
      - run: conda install -c conda-forge conda-workspaces sigstore
      - run: conda workspace lock --sign
```

Consumers should verify against the exact workflow identity they trust.
For GitHub Actions, the issuer is usually:

```text
https://token.actions.githubusercontent.com
```

The certificate identity is tied to the workflow and ref, for example:

```text
https://github.com/ORG/REPO/.github/workflows/release.yml@refs/heads/main
```

## Verify an archived workspace

Create an archive that includes a signed lockfile attestation:

```bash
conda workspace archive --lock --sign -o dist/my-project.tar.zst
```

Verify the bundled attestation before extraction:

```bash
conda workspace unarchive dist/my-project.tar.zst \
  --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Add `--receipt` when you also need an external integrity record for the
archive bytes:

```bash
conda workspace archive --lock --sign --receipt -o dist/my-project.tar.zst
conda workspace unarchive dist/my-project.tar.zst \
  --receipt \
  --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

## Choose the right check

Use `conda workspace verify` to check an existing workspace before doing
anything else.

Use `conda workspace install --locked --verify` when attestation
verification should gate a reproducible install.

Use `conda workspace unarchive --verify` when a signed workspace archive
is crossing a trust boundary.

Use archive receipts when exact archive-byte integrity matters. Use
workspace attestations when signer provenance for the manifest and
lockfile matters. Use both when you need both properties.
