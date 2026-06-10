---
title: "Resolver erasure is idempotent — already-absent is success"
scope: "file"
path:
  - "packages/effaced/src/effaced/resolvers/**"
  - "packages/effaced/src/effaced/saga/**"
  - "packages/effaced-stripe/src/effaced_stripe/**"
severity_min: "critical"
buckets: ["resolver-contract", "idempotency"]
enabled: true
---
## Instructions
Erasing an already-absent subject returns success (`already_absent=True`), never
an error. Flag resolver or saga code where a retry would error on "already gone"
or double-execute a side effect. Saga steps are retried; a step run twice must
land in the same state as run once.

## Examples
### Bad example
```python
async def erase(self, ref: SubjectRef) -> ErasureOutcome:
    customer = await self._client.get(ref.value)   # raises 404 on second run
    await self._client.delete(customer.id)
    return ErasureOutcome(already_absent=False)
```
### Good example
```python
async def erase(self, ref: SubjectRef) -> ErasureOutcome:
    customer = await self._client.find(ref.value)
    if customer is None:
        return ErasureOutcome(already_absent=True)   # idempotent success
    await self._client.delete(customer.id)
    return ErasureOutcome(already_absent=False)
```
