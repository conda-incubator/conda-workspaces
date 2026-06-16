# Sign and verify a workspace

This tutorial walks through the Sigstore attestation lifecycle for a
conda-workspaces project. You will create a lockfile, sign the manifest
and lockfile together, verify the signature policy, and use verification
to gate installation.

Workspace attestations are useful when a workspace moves between systems
or people and the receiver needs to know that a trusted identity signed
the exact `conda.toml` and `conda.lock` they are about to install.

## Prerequisites

- conda-workspaces installed
- Sigstore installed in the same environment
- A trusted Sigstore identity and issuer for verification

Install signing support with conda:

```bash
conda install -c conda-forge conda-workspaces sigstore
```

For pip installs, use the optional extra:

```bash
python -m pip install "conda-workspaces[signing]"
```

The examples below use environment variables for the verification
policy. Replace the values with the identity and issuer your release
process trusts:

```bash
export SIGSTORE_CERT_IDENTITY="https://github.com/ORG/REPO/.github/workflows/release.yml@refs/heads/main"
export SIGSTORE_OIDC_ISSUER="https://token.actions.githubusercontent.com"
```

## Step 1: Create a workspace

Start with a small project:

```bash
mkdir signed-workspace
cd signed-workspace
conda workspace init --name signed-workspace
conda workspace add "python>=3.12" "requests>=2"
```

`conda workspace add` updates the manifest, installs the default
environment, and writes `conda.lock`.

## Step 2: Sign the lockfile

Run `lock --sign` when you want solving and signing to be one release
step:

```bash
conda workspace lock --sign
```

This writes two files:

```text
conda.lock
conda.lock.sigstore.json
```

The JSON sidecar is a Sigstore bundle containing a DSSE-signed in-toto
Statement. The Statement records SHA-256 digests for the workspace
manifest and `conda.lock`.

If you already have the lockfile you want to release, sign it without
re-solving:

```bash
conda workspace attest
```

In a local shell, Sigstore may open an identity flow in your browser. In
CI, prefer the platform OIDC credential when one is available. Pass
`--identity-token` only when your CI system gives you an OIDC token
explicitly.

## Step 3: Verify the attestation

Verification requires the receiver to supply the trusted identity and
issuer:

```bash
conda workspace verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

This checks the Sigstore bundle, validates the in-toto payload, and
confirms that the current manifest and `conda.lock` match the signed
digests.

The trust policy comes from the verifier, not from the workspace. That
prevents a downloaded manifest from declaring which signer should be
trusted for itself.

## Step 4: See what invalidates the signature

Change the manifest without re-signing:

```bash
conda workspace add --no-install "httpx>=0.27"
```

Verification now fails because the manifest digest no longer matches the
attestation:

```bash
conda workspace verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

After reviewing the change, regenerate the lockfile and attestation:

```bash
conda workspace lock --sign
```

## Step 5: Gate installation

Use `install --locked --verify` when installation should proceed only
after freshness checks and attestation verification both pass:

```bash
conda workspace install --locked --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Use `--frozen --verify` only when you intentionally want to skip
manifest freshness checks while still requiring the Sigstore bundle to
match the files on disk.

## Step 6: Archive a signed workspace

Archives can carry the attestation alongside the manifest and lockfile:

```bash
conda workspace archive --lock --sign --receipt -o dist/signed-workspace.tar.zst
```

The receiving side verifies signer provenance before extraction:

```bash
conda workspace unarchive dist/signed-workspace.tar.zst \
  --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Use `--receipt` when you also need an integrity record for the exact
archive bytes. Use `--verify` when the receiver needs signer provenance
for the workspace manifest and lockfile.

## Next steps

- [Sign and verify workspace lockfiles](../how-to/workspace-attestations.md)
  for operational recipes
- [Workspace attestation reference](../reference/workspace-attestations.md)
  for the bundle format and verification contract
- [Archives](archives.md) for receipt and bundled-package workflows
