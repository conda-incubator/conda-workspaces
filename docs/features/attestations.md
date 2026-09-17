(workspace-attestations)=

# Workspace attestations

A workspace attestation is a Sigstore bundle for one manifest and the
canonical `conda.lock`. It records both SHA-256 digests in an in-toto
Statement and reports the identity and issuer authenticated by Sigstore.

Create or refresh the canonical lockfile and sign it in one command:

```bash
conda workspace lock --sign
```

Sign an existing lockfile without solving again:

```bash
conda workspace attest
```

Both commands write `conda.lock.sigstore.json` by default. Signing uses
conda-sigstore's ambient OIDC credential discovery. There is no command-line
option for passing an identity token.

## Verification and authorization

Verification always authenticates the certificate identity and OIDC issuer.
Pass the exact pair accepted by the receiver to apply an authorization rule:

```bash
conda workspace verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

The command checks the Sigstore bundle, workspace predicate, and the current
manifest and lockfile digests. When the signer flags are present, it also
requires the authenticated signer to match them. The policy comes from the
receiver. A downloaded workspace cannot declare itself trusted.

Use the same check as an installation gate:

```bash
conda workspace install --locked --verify \
  --cert-identity "$SIGSTORE_CERT_IDENTITY" \
  --cert-oidc-issuer "$SIGSTORE_OIDC_ISSUER"
```

Installation verifies the same bounded lockfile bytes that it parses and
installs. `--frozen --verify` keeps the signature check but skips the usual
manifest-to-lockfile satisfiability check.

## What each check covers

| Check | Binds | Reports an authenticated signer |
|---|---|---|
| Workspace attestation | Workspace manifest and canonical `conda.lock` | Yes |
| Archive receipt | Archive SHA-256, manifest, lockfile, and package inventory | No |
| Signed archive receipt | Archive SHA-256, manifest, lockfile, and package inventory | Yes |
| CEP 27 package attestation | One conda package artifact and publication statement | Yes |

These checks are complementary. `archive --sign` signs the archive receipt,
not the live-workspace predicate described on this page. Conda-sigstore's
per-package verifier is separate, disabled by default, and does not authorize
package signers. It requires conda's package-verifier hook, which current
released conda versions do not provide.

A valid Sigstore bundle proves neither that its signer is authorized nor that
the lockfile is the newest release. The explicit signer flags provide the
authorization rule for one command. Release or repository policy must decide
which attestation is current.

See [Sign and verify a workspace](../how-to/workspace-attestations.md) for
commands and [Workspace attestation reference](../reference/workspace-attestations.md)
for the statement and output contracts.
