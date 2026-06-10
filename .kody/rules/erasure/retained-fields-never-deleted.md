---
title: "Retained fields are never deleted by any erasure path"
scope: "file"
path:
  - "packages/effaced/src/effaced/erasure/**"
  - "packages/effaced/src/effaced/saga/**"
severity_min: "critical"
buckets: ["retention", "erasure-semantics"]
enabled: true
---
## Instructions
Fields declared `ErasureStrategy.RETAIN` must never be deleted by any code path;
the planner raises `RetentionViolationError` instead of guessing. A `RETAIN`
without an accompanying `RetentionPolicy` must be unrepresentable (enforced by a
validator). Flag any code that could delete a retained field, or that skips
recording the retention decision.

## Examples
### Bad example
```python
def plan_erasure(spec: PiiSpec) -> ErasurePlan:
    # Deletes every annotated field, ignoring RETAIN — a retention violation.
    return ErasurePlan(delete=[f.name for f in spec.fields])
```
### Good example
```python
def plan_erasure(spec: PiiSpec) -> ErasurePlan:
    for f in spec.fields:
        if f.strategy is ErasureStrategy.RETAIN and f.retention_policy is None:
            raise RetentionViolationError(f.name)
    return ErasurePlan(
        delete=[f.name for f in spec.fields if f.strategy is ErasureStrategy.DELETE],
    )
```
