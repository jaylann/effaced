# effaced-supabase

First-party [effaced](https://github.com/jaylann/effaced) resolvers for
Supabase: `SupabaseAuthResolver` reaches the subject's Supabase Auth
(`auth.users`) record via the Admin API, `SupabaseStorageResolver`
reaches the subject's objects in a Storage bucket via Supabase's
S3-compatible gateway, and `SupabasePostgrestResolver` reaches the
subject's PII in your application tables via the PostgREST data API.

- **Export (Art. 15):** the user's contact fields (`email`, `phone`).
- **Erase (Art. 17):** hard-deletes the user via
  `DELETE /auth/v1/admin/users/{id}` — the user and their identities are
  gone, not soft-deleted.

```bash
uv add effaced effaced-supabase
```

```python
from effaced import ResolverRegistry, SubjectRef
from effaced_supabase import SupabaseAuthResolver

registry = ResolverRegistry()
registry.register(
    SupabaseAuthResolver(
        base_url="https://<project-ref>.supabase.co",
        service_role_key="...",  # server-side only — see below
    )
)

# Refs of kind "supabase_auth" are routed to this resolver; the value is
# the GoTrue user id (the `auth.users` primary key).
ref = SubjectRef(kind="supabase_auth", value="00000000-0000-0000-0000-000000000000")
```

## Supabase Storage resolver

Subject-owned files (avatars, uploads) live in Storage buckets. The
storage resolver rides `effaced-s3`'s S3-compatible machinery, so it
ships behind an optional extra — auth-only installs stay httpx-only:

```bash
pip install "effaced-supabase[storage]"
```

It is **not** re-exported from `effaced_supabase`; import it directly so
auth-only installs import cleanly without the extra:

```python
from effaced_supabase.storage_resolver import SupabaseStorageResolver

registry.register(
    SupabaseStorageResolver(
        bucket="user-content",
        endpoint_url="https://<project_ref>.storage.supabase.co/storage/v1/s3",
        access_key_id="...",      # dashboard S3 access key — server-side only
        secret_access_key="...",
        region="<project-region>",
    )
)

# Refs of kind "supabase_storage"; the value is the subject's key prefix.
ref = SubjectRef(kind="supabase_storage", value="users/42/")
```

Authentication is a dashboard-issued **S3 access key** (Project Settings →
Storage → S3 Access Keys) — a root credential, server-side only. The
prefix must be non-blank and end with `/`, or construction-time and
call-time guards refuse it: an unterminated prefix matches sibling
subjects (`users/4` matches `users/42/...`). Supabase Storage has **no
versioning**, so erasure deletes the current objects under the prefix and
that is complete — the resolver never calls `ListObjectVersions`. Empty
listing is `already_absent=True`; partial batch failures retry to
convergence. See the
[Supabase guide](https://effaced.dev/effaced/docs/guides/supabase/) for
the full walkthrough.

## Supabase PostgREST resolver

A subject's PII usually lives in your own application tables, exposed over
Supabase's PostgREST data API (`/rest/v1/...`). `SupabasePostgrestResolver`
reaches those rows. It performs **no schema discovery** — you declare the
tables and columns explicitly, so the configuration is an auditable record
of which rows and columns the resolver reaches:

```python
from effaced import PiiCategory
from effaced_supabase import (
    PostgrestColumn,
    PostgrestTable,
    SupabasePostgrestResolver,
)

registry.register(
    SupabasePostgrestResolver(
        base_url="https://<project-ref>.supabase.co",
        service_role_key="...",  # server-side only — see below
        tables=[
            PostgrestTable(
                name="profiles",
                subject_column="user_id",
                columns=[
                    PostgrestColumn(name="full_name", category=PiiCategory.IDENTITY),
                    PostgrestColumn(name="email", category=PiiCategory.CONTACT),
                ],
            ),
            PostgrestTable(
                name="addresses",
                subject_column="owner_id",
                columns=[PostgrestColumn(name="line1", category=PiiCategory.CONTACT)],
            ),
        ],
    )
)

# Refs of kind "supabase_postgrest"; the value is the subject id matched
# against each table's subject_column.
ref = SubjectRef(kind="supabase_postgrest", value="...")
```

Per table, **export** issues
`GET /rest/v1/{table}?{subject_column}=eq.{id}&select=...` and emits one
record per populated declared column, sourced under the table name;
**erasure** issues `DELETE /rest/v1/{table}?{subject_column}=eq.{id}` with
`Prefer: return=representation`. PostgREST answers a no-match delete with an
empty representation (not a 404), so a subject whose every declared table
deletes nothing is `already_absent=True` — success, never an error. The
subject id rides the request as an `eq.<id>` query parameter and is matched
literally, so it never widens the filter or targets a sibling. Declaring no
tables raises `ConfigurationError` — a resolver that reaches nothing is a
wiring mistake.

## Service-role key — server-side only

The Admin API and the PostgREST data API both need the project's
**service-role key** (Dashboard → **Settings → API keys**); they reject
anon/publishable keys. That key bypasses Row Level Security everywhere, so
treat it like a root credential: server-side configuration only, never in
client bundles. A key without the required access surfaces as a
non-retryable `ResolverError` the first time the saga touches Supabase.
(The Storage resolver authenticates separately, with a dashboard-issued S3
access key.)

## What gets exported — and what never is

Exported, when populated: the user's top-level `email` and `phone`.
GoTrue stores unset contact fields as empty strings; those are treated
as "not held" and skipped.

Never exported:

- **`user_metadata` / `app_metadata`.** Their contents are defined by
  your application; the resolver cannot know what they hold. Export
  those fields through your own data map instead.
- **`identities`.** OAuth-provider payloads are provider-shaped and
  duplicate the top-level contact fields.

Changing the exported field set is behaviour under effaced's widened
SemVer: additions are MINOR, removals MAJOR.

## Covered surface

All three resolvers attest a `covered_surface` (the `AttestingResolver`
capability), built from the same field tuples their exporters use so
declaration and implementation cannot drift. `SupabaseAuthResolver`
declares `user.email` / `user.phone` and excludes `user_metadata` /
`app_metadata` / `identities` with reasons; `SupabaseStorageResolver`
declares the object globs and notes the ADR 0016 asymmetry — Supabase
Storage has no object versioning, so current-object deletion is complete
erasure; `SupabasePostgrestResolver` declares exactly the `{table}.{column}`
fields you configured and notes that it discovers nothing beyond them. The
shared
conformance suite proves every export stays within the declared surface
and never touches an exclusion. A covered surface declares *claimed*
reach; it cannot prove Supabase holds no personal data elsewhere, and is
not a compliance determination.

## Idempotency & error semantics

- Erasing a user GoTrue no longer knows is **success**
  (`already_absent=True`), never an error — saga retries depend on it.
- Rate limits (429), connection faults, and Supabase-side errors (5xx)
  propagate so the saga runner retries with backoff. Bad or
  under-permissioned keys and malformed requests raise `ResolverError`
  and abandon fast. The taxonomy keys on status codes only — GoTrue
  error-body shapes vary across versions.

## Testing

No live calls: pass `transport=` (any `httpx.BaseTransport`, e.g.
`httpx.MockTransport`) to fake the wire. The package is verified against
the shared `effaced.testing.ResolverConformanceSuite` — subclass it the
same way to certify your own resolver.

> **Not legal advice.** effaced provides technical mechanisms for
> implementing data-subject rights. It does not make you GDPR-compliant
> and does not constitute legal advice.

Licensed under Apache-2.0.
