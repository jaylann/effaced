# effaced-stripe

First-party Stripe resolver. Depends on `effaced` (workspace source) + `stripe` SDK.

- `StripeResolver` implements the `Resolver` protocol (structural; `test_resolver_conformance.py` runs the shared `effaced.testing.ResolverConformanceSuite`). Keep `name == "stripe"` stable forever — it's recorded in audits and outbox entries.
- Refs: `kind="stripe"`, `value=<customer id>`. Never accept or log emails/names.
- **Idempotency:** Stripe 404 on deletion ⇒ `ResolverErasure(already_absent=True)` — success, not an error. Transient Stripe errors (rate limit, 5xx) propagate for saga retry; only non-retryable misconfiguration raises `ResolverError` (taxonomy table in `errors.py`).
- Export returns what Stripe exposes (profile, addresses, payment-method metadata) — full card numbers don't exist via API and must never be implied. The exported field set lives in `export_records.py` and is behaviour under widened SemVer: removing/recategorising a field is MAJOR.
- Uses the **sync** SDK wrapped in `asyncio.to_thread` (the `*_async` methods need httpx/aiohttp, which are not deps). `client.v1.*` services require `stripe>=12.5` — don't lower the floor. SDK retries are off (`max_network_retries=0`); the saga runner owns retries (ADR 0010).
- Stripe objects are NOT dicts/Mappings (no `.get()`, missing keys raise `AttributeError`); convert at the boundary with `.to_dict()` before mapping.
- No live network calls in tests; inject `http_client=FakeStripeHTTPClient(...)` (`tests/fake_stripe_client.py`) — faking at the HTTP boundary exercises the SDK's real status→exception mapping.
