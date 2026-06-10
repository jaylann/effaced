# effaced-stripe

First-party Stripe resolver. Depends on `effaced` (workspace source) + `stripe` SDK.

- `StripeResolver` implements the `Resolver` protocol (structural; `test_resolver.py` guards conformance). Keep `name == "stripe"` stable forever — it's recorded in audits and outbox entries.
- Refs: `kind="stripe"`, `value=<customer id>`. Never accept or log emails/names.
- **Idempotency:** Stripe 404 on deletion ⇒ `ResolverErasure(already_absent=True)` — success, not an error. Transient Stripe errors (rate limit, 5xx) propagate for saga retry; only non-retryable misconfiguration raises `ResolverError`.
- Export returns what Stripe exposes (profile, addresses, payment-method metadata) — full card numbers don't exist via API and must never be implied.
- No live network calls in tests; fake the Stripe client.
