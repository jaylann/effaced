<!-- @kody-sync -->
# Audit trail (packages/effaced/src/effaced/audit/)

- Append-only by construction: flag any update/delete method appearing on a sink or event storage as a blocker.
- Events carry references and small scalars only — flag rich PII (emails, names, free-text content) in `AuditEvent.payload`.
- Every outcome must be recorded: success, failure, retention skip, retry abandonment. Flag code paths that can complete without emitting their event.
- `AuditEventType` members are format: removal/rename is breaking.
