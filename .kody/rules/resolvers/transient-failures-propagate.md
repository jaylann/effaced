---
title: "Transient resolver failures propagate; only non-retryable ones raise ResolverError"
scope: "file"
path:
  - "packages/effaced/src/effaced/resolvers/**"
  - "packages/effaced/src/effaced/saga/**"
  - "packages/effaced-stripe/src/effaced_stripe/**"
severity_min: "high"
buckets: ["resolver-contract"]
enabled: true
---
## Instructions
Transient failures (timeouts, rate limits, 5xx) must propagate so the saga can
retry them. Only genuinely non-retryable failures raise `ResolverError`. Flag
code that swallows a transient error (turning a retryable failure into a false
success) or that wraps a transient error as `ResolverError` (aborting a retry
that would have succeeded).

## Examples
### Bad example
```python
try:
    await self._client.delete(ref.value)
except TimeoutError:
    return ErasureOutcome(already_absent=True)  # swallows a retryable failure
```
### Good example
```python
try:
    await self._client.delete(ref.value)
except TimeoutError:
    raise                       # transient -> propagate for saga retry
except InvalidCustomerError as exc:
    raise ResolverError(str(exc)) from exc   # non-retryable
```
