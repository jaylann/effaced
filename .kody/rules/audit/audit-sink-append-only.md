---
title: "Audit sinks and event storage are append-only by construction"
scope: "file"
path:
  - "packages/effaced/src/effaced/audit/**"
severity_min: "critical"
buckets: ["audit-integrity"]
enabled: true
---
## Instructions
The audit trail is append-only by construction. Flag any `update`, `delete`,
`edit`, `purge`, or in-place mutation method appearing on an `AuditSink` or on
event storage as a blocker — the surface must not exist, not merely go unused.

## Examples
### Bad example
```python
class AuditSink(Protocol):
    async def append(self, event: AuditEvent) -> None: ...
    async def delete(self, event_id: str) -> None: ...   # mutating surface
```
### Good example
```python
class AuditSink(Protocol):
    async def append(self, event: AuditEvent) -> None: ...
    # append-only: no update/delete/purge by design
```
