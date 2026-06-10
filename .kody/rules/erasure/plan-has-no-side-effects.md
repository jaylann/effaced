---
title: "Erasure plans are inspectable before execution (plan() is pure)"
scope: "file"
path:
  - "packages/effaced/src/effaced/erasure/**"
  - "packages/effaced/src/effaced/saga/**"
severity_min: "medium"
buckets: ["erasure-semantics"]
enabled: true
---
## Instructions
An erasure plan must be inspectable before anything is executed: `plan()` (and
anything that builds a plan) has no side effects — no deletes, no writes, no
outbox enqueues, no external calls. Flag mutation or I/O performed while a plan
is merely being constructed.

## Examples
### Bad example
```python
def plan(self, ref: SubjectRef) -> ErasurePlan:
    rows = self._select_rows(ref)
    self._delete(rows)               # side effect while "planning"
    return ErasurePlan(steps=rows)
```
### Good example
```python
def plan(self, ref: SubjectRef) -> ErasurePlan:
    rows = self._select_rows(ref)    # read-only; execution happens later
    return ErasurePlan(steps=rows)
```
