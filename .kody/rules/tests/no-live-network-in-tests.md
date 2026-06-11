---
title: "Tests make no live network calls"
scope: "file"
path:
  - "packages/effaced/tests/**"
  - "packages/effaced-stripe/tests/**"
severity_min: "high"
buckets: ["test-coverage"]
enabled: true
---
## Instructions
No live network calls in tests, ever. External systems (Stripe, etc.) are faked
behind the `Resolver` protocol. Flag a real HTTP client, a live SDK call, or a
hostname/URL being dialled inside a test — it makes the suite flaky and leaks
beyond the process.

## Examples
### Bad example
```python
def test_stripe_erase():
    client = stripe.StripeClient(api_key=os.environ["STRIPE_KEY"])  # live call
    StripeResolver(client).erase(ref)
```
### Good example
```python
def test_stripe_erase():
    resolver = StripeResolver(FakeStripeClient(customers={"cus_1": {}}))
    outcome = resolver.erase(SubjectRef(kind="stripe", value="cus_1"))
    assert outcome.already_absent is False
```
