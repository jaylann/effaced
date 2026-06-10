---
title: "Resolvers receive opaque SubjectRef, never rich PII"
scope: "file"
path:
  - "packages/effaced/src/effaced/resolvers/**"
  - "packages/effaced-stripe/src/effaced_stripe/**"
severity_min: "high"
buckets: ["resolver-contract", "pii-handling"]
enabled: true
---
## Instructions
Resolvers operate on `SubjectRef` — opaque identifiers. Flag any resolver method
that accepts, logs, or stores rich PII (emails, names, message bodies, raw field
values), and any logging of `SubjectRef` contents at a level that would persist
it. The resolver knows a reference, not the subject's data.

## Examples
### Bad example
```python
async def erase(self, email: str, full_name: str) -> ErasureOutcome:
    logger.info("erasing %s (%s)", full_name, email)   # PII in signature + logs
    ...
```
### Good example
```python
async def erase(self, ref: SubjectRef) -> ErasureOutcome:
    logger.info("erasing subject ref kind=%s", ref.kind)   # opaque, no PII
    ...
```
