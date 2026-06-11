# effaced-s3

First-party [effaced](https://github.com/jaylann/effaced) resolver for
Amazon S3 — export and erase a data subject's user-generated objects
(avatars, uploads, attachments) held under a key prefix.

- **Export (Art. 15):** every object under the subject's prefix — key,
  size, content type, last-modified, user metadata, and (by default) the
  object content itself, base64-encoded.
- **Erase (Art. 17):** every object **version and delete marker** under
  the prefix is permanently deleted — a plain delete on a versioned
  bucket only hides data; this resolver destroys it.

```bash
uv add effaced effaced-s3
```

```python
from effaced import ResolverRegistry, SubjectRef
from effaced_s3 import S3Resolver

registry = ResolverRegistry()
registry.register(S3Resolver(bucket="my-app-user-content"))

# Refs of kind "s3" are routed to this resolver; the value is the key
# prefix that scopes the subject's objects.
ref = SubjectRef(kind="s3", value="users/42/")
```

Credentials come from the standard AWS chain (environment, shared
config, instance role). For custom endpoints or scoped sessions
(MinIO, Cloudflare R2, localstack), construct your own client and pass
it via `client=`.

## Prefix scoping — the one rule that matters

The subject ref's `value` is a key prefix, and the resolver touches
**only** keys under it. A blank or whitespace prefix raises
`ResolverError` before any S3 call — the resolver will never enumerate
or erase a whole bucket. Design your key layout so each subject's
objects live under one prefix (`users/{id}/`), and make sure prefixes
can't collide (`users/1` also matches `users/10/...` — keep the
trailing delimiter in the ref value).

## IAM setup

Grant the resolver's credentials exactly what it uses, scoped to the
bucket (and prefix pattern, if your layout allows):

| Action | Used for |
|---|---|
| `s3:ListBucket` + `s3:ListBucketVersions` | enumerating the subject's objects |
| `s3:GetObject` | object content + metadata in exports |
| `s3:DeleteObject` + `s3:DeleteObjectVersion` | erasure across all versions |

A missing permission surfaces as a non-retryable `ResolverError` the
first time the saga touches S3.

## What gets exported — and the content default

Per object: `key`, `size`, `content_type`, `last_modified`, each user
metadata entry (`x-amz-meta-*`), and `content_base64` — a faithful copy
of the object body.

Content is included **by default** because for user-generated objects
the bytes usually *are* the personal data (an avatar is a photo of the
subject); metadata alone is not a copy of it. Pass
`include_content=False` for metadata-only exports **only when you
provide the files through another complete, retainable channel** —
whether that satisfies an access request is a determination you (the
controller) make, not this library.

`max_object_bytes=` caps how large an object the export will load; an
object over the cap fails the export loudly (`ResolverError`, surfacing
as `incomplete_sources` on the bundle) — never a silently thinned
bundle.

Exports cover **current** object versions; erasure destroys **all**
versions. Changing the exported field set is behaviour under effaced's
widened SemVer: additions are MINOR, removals MAJOR.

## Idempotency & error semantics

- Erasing a prefix S3 no longer holds anything under is **success**
  (`already_absent=True`), never an error — saga retries depend on it.
- A batch delete that partially fails keeps deleting the rest, then
  raises so the saga retries; already-deleted keys re-delete as no-ops,
  so retries converge.
- Throttling (`SlowDown`), connection faults, and S3-side errors (5xx)
  propagate so the saga runner retries with backoff. Bad credentials,
  missing permissions, and missing buckets raise `ResolverError` and
  abandon fast. SDK-internal retries are disabled; the saga runner owns
  retry policy.

## S3-compatible stores

Erasure semantics assume AWS behaviour: `ListObjectVersions` works on
unversioned buckets (reporting `VersionId="null"`). Some S3-compatible
stores diverge on versioning APIs — verify yours before relying on
all-versions erasure.

## Testing

No live calls: pass `client=` (anything satisfying
`effaced_s3.object_client.S3ObjectClient`) to fake the transport. The
package is verified against the shared
`effaced.testing.ResolverConformanceSuite` — subclass it the same way to
certify your own resolver.

> **Not legal advice.** effaced provides technical mechanisms for
> implementing data-subject rights. It does not make you GDPR-compliant
> and does not constitute legal advice.

Licensed under Apache-2.0.
