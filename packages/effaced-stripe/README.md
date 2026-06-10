# effaced-stripe

First-party [effaced](https://github.com/jaylann/effaced) resolver for
Stripe — export and erase a data subject's billing PII held in Stripe.

```bash
uv add effaced effaced-stripe
```

```python
from effaced import ResolverRegistry
from effaced_stripe import StripeResolver

registry = ResolverRegistry()
registry.register(StripeResolver(api_key="rk_live_..."))
```

Idempotency contract: erasing a customer Stripe no longer knows is
**success** (`already_absent=True`), never an error.

> **Not legal advice.** effaced provides technical mechanisms for
> implementing data-subject rights. It does not make you GDPR-compliant
> and does not constitute legal advice.

Licensed under Apache-2.0.
