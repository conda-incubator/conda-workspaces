# Generate a workspace SBOM

Install conda-sboms in the same environment that owns the `conda` executable
and conda-workspaces:

```console
conda activate base
conda pypi install "conda-sboms>=0.3.0"
```

If `conda pypi` is unavailable, install the wheel in that same environment:

```console
python -m pip install "conda-sboms>=0.3.0"
```

## Generate from a lockfile

Create or refresh `conda.lock`, then export the default environment for the
current platform:

```console
conda workspace lock
conda workspace sbom --file exports/default.cdx.json
```

Select another environment and platform when needed:

```console
conda workspace sbom \
  --environment test \
  --platform linux-64 \
  --file exports/test-linux-64.cdx.json
```

The command reads existing lock metadata. It does not solve, update the
lockfile, or fetch package archives. For a rich platform such as
`linux-64-cuda`, pass either its declared name or its backing subdir. Use the
declared name when multiple variants share one subdir.

## Generate from an installed prefix

Use `--from-prefix` to inventory the selected installed workspace environment:

```console
conda workspace sbom \
  --environment test \
  --from-prefix \
  --file exports/test-installed.cdx.json
```

Prefix mode supports the host platform only. A different `--platform` value
fails instead of describing the prefix as another platform.

## Add product and author metadata

Supply identities only when you can establish them for the product and the
people or organizations responsible for the SBOM:

```console
conda workspace sbom \
  --file exports/acme-runtime.cdx.json \
  --product-name "Acme Runtime" \
  --product-version "2026.08" \
  --product-manufacturer "Acme GmbH" \
  --product-manufacturer-url "https://acme.example" \
  --author-name "Alice Example" \
  --author-email "alice@acme.example" \
  --author-organization "Acme Product Security" \
  --author-organization-url "https://acme.example/security"
```

The product name and version must be supplied together. A manufacturer
requires both product values. A manufacturer URL requires the manufacturer
name. An author email requires an author name. An author organization URL
requires the author organization name.

When no metadata flags are passed, conda-sboms reads its active plugin
settings. Passing any metadata flag replaces those settings for this export.
Unspecified values remain unset.

## Make the output reproducible

Pass `--reproducible` to omit `metadata.timestamp` and record
`cdx:reproducible=true`:

```console
conda workspace sbom --reproducible \
  --file exports/default.cdx.json
```

The flag takes precedence over `SOURCE_DATE_EPOCH`. Use
`SOURCE_DATE_EPOCH` instead when consumers require a meaningful, stable
timestamp:

```console
SOURCE_DATE_EPOCH=1787184000 conda workspace sbom \
  --file exports/default.cdx.json
```

Without `--reproducible`, an invalid, negative, or unsupported value fails
before output is written. Byte-for-byte reproducibility also requires unchanged
inputs and the same conda-sboms and serializer versions.

See [](../features/export.md) for source behavior and coverage limits, and
[](../reference/cli.md) for every command option.
