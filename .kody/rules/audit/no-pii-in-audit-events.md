---
title: "Audit events carry references and scalars, never rich PII"
scope: "file"
path:
  - "packages/effaced/src/effaced/audit/**"
severity_min: "high"
buckets: ["audit-integrity", "pii-handling"]
enabled: true
---
## Instructions
Audit events carry opaque references and small scalars only. Flag rich PII —
emails, names, addresses, free-text message bodies, raw field values — placed in
`AuditEvent.payload` or any event field. The trail records that something
happened to a subject reference, not the subject's data.

## Examples
### Bad example
```python
event = AuditEvent(
    type=AuditEventType.ERASURE_COMPLETED,
    subject_ref=ref,
    payload={"email": "alice@example.com", "deleted_message": body},  # PII
)
```
### Good example
```python
event = AuditEvent(
    type=AuditEventType.ERASURE_COMPLETED,
    subject_ref=ref,
    payload={"fields_erased": 3, "resolver": "stripe"},  # references + scalars
)
```
