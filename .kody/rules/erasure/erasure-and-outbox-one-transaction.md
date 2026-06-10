---
title: "Local erasure and its outbox enqueue share one transaction"
scope: "file"
path:
  - "packages/effaced/src/effaced/erasure/**"
  - "packages/effaced/src/effaced/saga/**"
severity_min: "critical"
buckets: ["erasure-semantics", "atomicity"]
enabled: true
---
## Instructions
Local erasure steps run in ONE atomic transaction, and the outbox entries that
fan the erasure out to external systems must enqueue in that SAME transaction.
Any code that commits the local deletion separately from enqueuing the outbox
work reintroduces the half-erased-state bug (local rows gone, downstream never
told, or vice-versa). Flag a second `commit()`/session boundary between the two.

## Examples
### Bad example
```python
async def erase(self, ref: SubjectRef) -> None:
    async with self.session.begin():
        await self._delete_local(ref)          # committed here
    async with self.session.begin():
        await self._enqueue_outbox(ref)        # separate txn: can be lost
```
### Good example
```python
async def erase(self, ref: SubjectRef) -> None:
    async with self.session.begin():           # one atomic boundary
        await self._delete_local(ref)
        await self._enqueue_outbox(ref)
```
