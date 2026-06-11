# effaced-supabase

First-party [effaced](https://github.com/jaylann/effaced) resolver for
Supabase — export and erase a data subject's record in Supabase Auth
(`auth.users`) via the Admin API.

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

## Service-role key — server-side only

The Admin API rejects anon/publishable keys; this resolver needs the
project's **service-role key** (Dashboard → **Settings → API keys**).
That key bypasses Row Level Security everywhere, so treat it like a root
credential: server-side configuration only, never in client bundles. A
key without admin access surfaces as a non-retryable `ResolverError` the
first time the saga touches Supabase.

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
