# 0016. Supabase Storage rides effaced-s3's S3-compatible machinery

- **Status:** accepted
- **Date:** 2026-06-12

## Context

Supabase Storage exposes an S3-compatible gateway
(`https://<project_ref>.storage.supabase.co/storage/v1/s3`) that speaks
`ListObjectsV2`, `GetObject`, `HeadObject`, `DeleteObject`, and
`DeleteObjects` over SigV4. Issue #57 wants a `SupabaseStorageResolver`
that brings a subject's storage objects into Art. 15 exports and Art. 17
erasures, alongside `SupabaseAuthResolver` in `effaced-supabase`.

`effaced-s3` already solved the hard parts: the prefix guard against
whole-bucket and sibling-prefix bleed, the paginated export collector,
the batched `delete_objects` that keeps going past per-key failures, and
the `botocore` error taxonomy (retry vs. abandon). Re-implementing them
in `effaced-supabase` would fork the very machinery whose correctness is
a legal-defensibility property — two copies to keep byte-identical under
the no-bleed and idempotency guarantees.

Two facts about the gateway diverge from AWS and shape the decision:

- **No object versioning.** The gateway does not implement
  `ListObjectVersions`, and Supabase Storage has no versions or delete
  markers. So `S3Resolver`'s all-versions erasure path does not apply —
  deleting the *current* objects under the prefix IS complete erasure.
- **Path-style addressing required**, and authentication is a
  dashboard-issued S3 access key (a root credential, server-side only).

## Decision

### `effaced-supabase[storage]` depends on `effaced-s3`; the parts it rides are public

The machinery `effaced-s3` already held internal is promoted to its
`__all__` as a forever-stable surface, so a sibling package can build on
it without forking:

- `S3ObjectClient` — the typed five-call client protocol;
- `checked_prefix` — the blank/`"/"`-termination bleed guard;
- `collect_object_records` — the paginated, size-capped export collector
  (now taking a keyword-only `source` label so each record records which
  system held the object);
- `iter_current_objects`, `collect_version_identifiers` — the listing
  helpers;
- `delete_in_batches` — the batched `delete_objects` that accumulates
  per-key error codes and keeps deleting past failures;
- `error_code`, `is_nonretryable`, `NONRETRYABLE_CODES` — the taxonomy.

The `S3Resolver` refactor that extracted these is behaviour-identical:
every error message and `ResolverErasure.detail` string stays
byte-identical, every existing `effaced-s3` test passes unchanged. That
extraction is therefore an additive MINOR for `effaced-s3` (a public
surface grew); any drift in what `S3Resolver` deletes or exports would
have been MAJOR, hence the byte-identical requirement.

### The dependency is an optional extra, not a hard one

Auth-only installs must keep importing `effaced_supabase` with httpx
alone — adding `boto3` to every Supabase install would be a regression
for the common case. So the storage parts live behind
`pip install "effaced-supabase[storage]"` (extra: `effaced-s3`,
`boto3>=1.35`). `SupabaseStorageResolver` lives in its own module and is
**not** re-exported from `effaced_supabase.__init__`; the public import is
the deep `from effaced_supabase.storage_resolver import
SupabaseStorageResolver`. The module raises a pointed `ImportError`
naming the extra when `effaced-s3` is absent.

### No-versioning erasure semantics

`SupabaseStorageResolver.erase_subject` lists the **current** objects via
`iter_current_objects`, builds `Key`-only identifiers, and deletes them
with `delete_in_batches`. It NEVER calls `list_object_versions` — the
gateway does not implement it, and there are no versions to sweep, so
current-object deletion is complete erasure. The idempotency and
partial-failure contract is otherwise identical to `S3Resolver`: an empty
listing is `already_absent=True` (success), mixed transient per-key
failures keep deleting then raise `PartialEraseError` (retryable), and an
all-non-retryable batch raises `ResolverError`.

### Fixed resolver name

The resolver's `name` is `"supabase_storage"`, fixed forever (refs of
`kind="supabase_storage"` route to it — ADR 0008). Recorded in audits and
outbox entries, so renaming it is MAJOR.

## Consequences

- One implementation of the bleed guard, export collector, batched
  delete, and taxonomy serves both AWS S3 and Supabase Storage; the
  no-bleed and idempotency proofs are written once and inherited.
- The promoted `effaced-s3` surface is now load-bearing public API:
  changing those signatures is breaking for `effaced-supabase` and any
  third party riding the same parts.
- A new S3-compatible target (MinIO, R2) can follow the same pattern: a
  thin resolver over the public machinery, versioned or not.
- `effaced-s3` is created per call inside the resolver (never cached on
  the instance — ADR 0006), and SDK retries are off so the saga runner
  owns retry (ADR 0010), exactly as `S3Resolver` does.
