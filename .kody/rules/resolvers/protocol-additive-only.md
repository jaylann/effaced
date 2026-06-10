---
title: "Resolver and AuditSink protocols evolve additively only"
scope: "file"
path:
  - "packages/effaced/src/effaced/resolvers/**"
  - "packages/effaced/src/effaced/audit/**"
  - "packages/effaced-stripe/src/effaced_stripe/**"
severity_min: "critical"
buckets: ["resolver-contract", "breaking-change"]
enabled: true
---
## Instructions
`Resolver` and `AuditSink` are public API with the strictest stability promise:
additive evolution only — new methods must be optional with a default
implementation. Flag ANY signature change to an existing protocol method
(renamed/added/removed/retyped parameter, changed return type) as a blocker.

## Examples
### Bad example
```python
class Resolver(Protocol):
    # `dry_run` added with no default — every existing implementer breaks.
    async def erase(self, ref: SubjectRef, dry_run: bool) -> ErasureOutcome: ...
```
### Good example
```python
class Resolver(Protocol):
    async def erase(self, ref: SubjectRef) -> ErasureOutcome: ...
    async def health_check(self) -> bool:        # new, optional with default
        return True
```
