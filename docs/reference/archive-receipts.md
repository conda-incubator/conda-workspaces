# Archive receipt reference

This page describes the receipt statement written by
`conda workspace archive --receipt` and embedded in the Sigstore bundle created
by `conda workspace archive --sign`. Receivers verify an unsigned receipt with
`conda workspace unarchive --receipt` or a signed receipt with
`conda workspace unarchive --verify`.

Unsigned archive receipts are sidecar JSON documents. The same
[in-toto Statement v1][in-toto-statement] is the DSSE payload in a signed
receipt bundle. Its conda-workspaces predicate binds a workspace archive to the
manifest, lockfile, and package inventory it was created from.

[in-toto-statement]: https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md

## Status

| Field | Value |
|---|---|
| Format | Workspace archive receipt |
| Format version | `1` |
| JSON schema | `https://conda-incubator.github.io/conda-workspaces/workspace-archive-receipt-1.schema.json` |
| Source schema | [`schema/workspace-archive-receipt-1.schema.json`](https://github.com/conda-incubator/conda-workspaces/blob/main/schema/workspace-archive-receipt-1.schema.json) |
| Statement type | `https://in-toto.io/Statement/v1` |
| Predicate type | `https://conda-incubator.github.io/conda-workspaces/workspace-archive-receipt-1.schema.json` |
| Producer | `conda workspace archive --receipt [PATH]` or `--sign` |
| Consumer | `conda workspace unarchive ARCHIVE --receipt [PATH]` or `--verify` |

## File naming

When `--receipt` is passed without a path, conda-workspaces writes or
reads a sibling file named after the archive:

```text
my-project.tar.zst
my-project.tar.zst.receipt.json
```

Pass an explicit path to store the receipt elsewhere:

```bash
conda workspace archive --receipt attestations/my-project.json -o my-project.tar.zst
conda workspace unarchive my-project.tar.zst --receipt attestations/my-project.json
```

The receipt path must be separate from the archive path.

`--sign` writes a sibling Sigstore Bundle v0.3 sidecar by default:

```text
my-project.tar.zst
my-project.tar.zst.sigstore.json
```

Pass `--attestation PATH` to `archive --sign` and `unarchive --verify` when the
bundle is stored elsewhere. A separate unsigned receipt is optional in this
workflow. If one is supplied during verification, its payload must match the
signed receipt.

## Statement structure

A receipt is a JSON object with the in-toto Statement fields
`_type`, `subject`, `predicateType`, and `predicate`.

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "my-project.tar.zst",
      "digest": {
        "sha256": "<archive sha256>"
      }
    },
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
  "predicateType": "https://conda-incubator.github.io/conda-workspaces/workspace-archive-receipt-1.schema.json",
  "predicate": {
    "archive": {
      "formatVersion": 1,
      "options": {}
    },
    "workspace": {
      "manifest": "conda.toml",
      "lockfile": "conda.lock"
    },
    "environments": []
  }
}
```

Receipts are written as stable, sorted, UTF-8 JSON. The loader rejects
duplicate JSON object keys because duplicate keys can make an integrity
document ambiguous.

## Subjects

The `subject` array records SHA-256 digests for:

- the archive file, using the archive basename as the subject name
- the workspace manifest path as stored inside the archive
- the `conda.lock` path as stored inside the archive

Receipt creation requires the selected archive filters to include the
workspace manifest and `conda.lock`. If `[workspace.archive].include`,
`[workspace.archive].exclude`, or `--exclude` would omit either file,
the archive command fails before writing the archive or receipt.

## Predicate

The predicate contains three sections.

| Section | Required fields | Description |
|---|---|---|
| `archive` | `formatVersion` | Receipt format version. `options` records archive options such as `bundle`, `lock`, `include`, `exclude`, and `compressionLevel` when available. |
| `workspace` | `manifest`, `lockfile` | POSIX archive-relative paths to the manifest and lockfile that must verify after extraction. |
| `environments` | `name`, `packages` | Lockfile-derived package inventory for each workspace environment. |

Environment records may include `prefix`. Prefixes inside the workspace
are stored as archive-relative POSIX paths such as
`.conda/envs/default`; external runtime prefixes remain absolute, using
POSIX or Windows syntax as appropriate.

Package records are normalized from `conda.lock`. Records may include:

| Field | Description |
|---|---|
| `name` | Package name |
| `version` | Package version |
| `build` | Package build string |
| `build_number` | Package build number |
| `subdir` | Conda platform subdir |
| `channel` | Package channel URL |
| `url` | Package artifact URL |
| `fn` | Package artifact filename |
| `sha256` | Package artifact SHA-256 digest |
| `md5` | Package artifact MD5 digest |

Package URLs and channel URLs are redacted before they are written to a
receipt. Embedded credentials, Anaconda tokens, query strings, and URL
fragments are removed.

## Verification

`conda workspace unarchive ARCHIVE --receipt [PATH]` verifies in this
order:

1. Load the receipt JSON and reject duplicate object keys.
2. Validate the in-toto Statement type, predicate type, and receipt
   format version.
3. Verify the archive file's SHA-256 digest before extraction.
4. Extract to a temporary staging directory under the target parent,
   using the same archive path traversal protections as regular
   extraction.
5. Verify the extracted manifest and lockfile SHA-256 digests.
6. Recompute the package inventory from the extracted `conda.lock` and
   compare it with the receipt.
7. Move the staged directory into the requested target.

Verified extraction requires the target path to be absent. Existing links,
files, and directories, including empty directories, are rejected. This
prevents an attacker from satisfying receipt paths with pre-existing files
outside the staged extraction.

Pass `--require-sha256` with `--receipt` to require every compared
package record to include SHA-256. Without it, the receipt still
compares all package identity and digest fields that are present.

## Signed receipts

`conda workspace archive --sign` wraps the receipt payload in a DSSE-signed
Sigstore bundle. `conda workspace unarchive --verify` verifies the bundle,
requires the receiver's exact certificate identity and OIDC issuer, then runs
the same archive, manifest, lockfile, and inventory checks as unsigned receipt
verification.

`archive --sign` builds the archive in private staging. It computes and signs
the receipt before publishing the archive, optional receipt file, or Sigstore
sidecar. If a later publication fails, the command restores an earlier output
only when it is still the generation that the command published. Concurrent
replacements are preserved. The same rule applies to a canonical lockfile
generated by `--lock --sign`.

## Trust model

Unsigned archive receipts are integrity documents, not signatures. They detect
whether the archive, extracted manifest, extracted lockfile, or lockfile
package inventory differs from the receipt, but they do not prove who
created the receipt.

For provenance-sensitive workflows, use `archive --sign` and give the receiver
an expected signer identity and issuer, or distribute an unsigned receipt
through an independent trusted channel.

For air-gapped workflows, pair `--receipt` with `--bundle` so the
archive carries the package artifacts and the receipt carries the
lockfile inventory that should describe them. `unarchive` only primes a
conda package cache from bundled packages after receipt verification.
