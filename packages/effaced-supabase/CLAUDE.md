# effaced-supabase

First-party Supabase resolvers. Depends on `effaced` (workspace source) + `httpx`. Hosts `SupabaseAuthResolver` today; storage (#57) and postgrest (#70) resolvers land here later — `errors.py` is deliberately resolver-agnostic for that.

- `SupabaseAuthResolver` implements the `Resolver` protocol (structural; `test_supabase_auth_conformance.py` runs the shared `effaced.testing.ResolverConformanceSuite`). Keep `name == "supabase_auth"` stable forever — it's recorded in audits and outbox entries.
- Refs: `kind="supabase_auth"`, `value=<gotrue user id>`. Never accept or log emails/phones.
- **Idempotency:** GoTrue 404 on get/delete ⇒ absent — export returns empty, erasure reports `already_absent=True`; success, not an error. Transient faults (429, 5xx, connection) propagate for saga retry; other 4xx raise `ResolverError` (taxonomy table in `errors.py`). The taxonomy keys on status codes only — GoTrue error-body shapes vary across versions.
- Export is `user.email` + `user.phone` (CONTACT) only. `user_metadata`/`app_metadata` are caller-defined and never exported; `identities` is provider-shaped and skipped. The field set lives in `auth_export_records.py` and is behaviour under widened SemVer: removing/recategorising a field is MAJOR. GoTrue's `""` for unset contact fields means "not held" — skipped, not exported.
- Sync `httpx` wrapped in `asyncio.to_thread`; a fresh client per call — nothing loop- or connection-bound cached on the instance (ADR 0006). Both `Authorization: Bearer` and `apikey` headers carry the service-role key (GoTrue authorizes the former, Supabase's gateway routes on the latter).
- Propagated `httpx.HTTPStatusError` messages embed the request URL (and thus the user id); that lands next to the subject ref in the outbox row, so it's accepted — the no-PII pin covers `ResolverError` messages only (same posture as stripe).
- No live network calls in tests; inject `transport=FakeGoTrueTransport(...)` (`tests/fake_gotrue_transport.py`) — faking at the httpx transport boundary exercises the real request pipeline.
