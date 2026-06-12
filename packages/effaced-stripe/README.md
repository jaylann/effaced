# effaced-stripe

First-party [effaced](https://github.com/jaylann/effaced) resolver for
Stripe — export and erase a data subject's billing PII held in Stripe.

- **Export (Art. 15):** customer profile, addresses, and payment-method
  metadata.
- **Erase (Art. 17):** Stripe customer deletion, which Stripe itself
  implements as a GDPR-aware redaction.

```bash
uv add effaced effaced-stripe
```

```python
from effaced import ResolverRegistry, SubjectRef
from effaced_stripe import StripeResolver

registry = ResolverRegistry()
registry.register(StripeResolver(api_key="rk_live_..."))

# Refs of kind "stripe" are routed to this resolver; the value is the
# Stripe customer id.
ref = SubjectRef(kind="stripe", value="cus_...")
```

## Restricted-key setup

Don't hand the resolver your full secret key. Create a restricted key
(Dashboard → **Developers → API keys → Create restricted key**) with
exactly the permissions the resolver uses:

| Permission | Access | Used for |
|---|---|---|
| Customers | **Write** | retrieve for export, delete for erasure |
| Payment Methods | **Read** | payment-method metadata in exports |

Everything else stays **None**. A key missing one of these surfaces as a
non-retryable `ResolverError` the first time the saga touches Stripe.

## What gets exported — and what never is

Exported, when populated: customer `email`, `name`, `phone`, billing and
shipping addresses, and per payment method its `type`, card metadata
(`brand`, `last4`, `exp_month`, `exp_year`), and billing details.

Never exported:

- **Full card numbers.** Stripe does not expose PANs over its API, so no
  export from this resolver can ever contain one — don't let anyone tell
  you otherwise.
- **`metadata` dicts.** Their contents are defined by your application;
  the resolver cannot know what they hold. Export those fields through
  your own data map instead.

Changing the exported field set is behaviour under effaced's widened
SemVer: additions are MINOR, removals MAJOR.

## Covered surface

`StripeResolver.covered_surface` (the `AttestingResolver` capability)
declares the Stripe fields this resolver claims to reach — built from the
same field tuples the exporter uses, so the two cannot drift — plus the
explicit exclusions (`customer.metadata.*`, Stripe-retained event
payloads, full card numbers). The shared conformance suite proves every
export stays within the declared surface and never touches an exclusion.
It declares *claimed* reach; it cannot prove Stripe holds no personal
data elsewhere, and is not a compliance determination.

## Idempotency & error semantics

- Erasing a customer Stripe no longer knows is **success**
  (`already_absent=True`), never an error — saga retries depend on it.
- Rate limits (429), connection faults, and Stripe-side errors (5xx)
  propagate so the saga runner retries with backoff. Bad or
  under-permissioned keys and malformed requests raise `ResolverError`
  and abandon fast. SDK-internal retries are disabled; the saga runner
  owns retry policy.

## Testing

No live calls: pass `http_client=` (any `stripe.HTTPClient`) to fake the
transport. The package is verified against the shared
`effaced.testing.ResolverConformanceSuite` — subclass it the same way to
certify your own resolver.

> **Not legal advice.** effaced provides technical mechanisms for
> implementing data-subject rights. It does not make you GDPR-compliant
> and does not constitute legal advice.

Licensed under Apache-2.0.
