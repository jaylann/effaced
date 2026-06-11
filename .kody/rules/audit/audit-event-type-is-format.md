---
title: "AuditEventType members are format — removal or rename is breaking"
scope: "file"
path:
  - "packages/effaced/src/effaced/audit/**"
severity_min: "high"
buckets: ["audit-integrity", "breaking-change"]
enabled: true
---
## Instructions
`AuditEventType` members are part of the on-the-wire/at-rest format. Adding a
member is MINOR; removing or renaming one is breaking, because persisted events
reference it. Flag removal or rename of an existing member without a breaking
declaration.

## Examples
### Bad example
```python
class AuditEventType(StrEnum):
    ERASURE_DONE = "erasure_done"   # renamed from ERASURE_COMPLETED — breaks
    EXPORT_COMPLETED = "export_completed"
```
### Good example
```python
class AuditEventType(StrEnum):
    ERASURE_COMPLETED = "erasure_completed"
    EXPORT_COMPLETED = "export_completed"
    CONSENT_WITHDRAWN = "consent_withdrawn"   # additive — MINOR
```
