<!-- @kody-sync -->
# Resolver contract (packages/effaced/src/effaced/resolvers/, packages/effaced-stripe/)

- `Resolver` and `AuditSink` protocols are public API: additive evolution only (new optional methods with defaults). Flag ANY signature change to existing protocol methods as a blocker.
- Idempotency: erasing an already-absent subject returns success (`already_absent=True`), never an error. Flag resolver code where a retry would error or double-execute.
- Transient failures (timeouts, rate limits) must propagate for saga retry; only non-retryable failures raise `ResolverError`.
- Resolvers receive `SubjectRef` (opaque identifiers) — flag any API accepting or logging rich PII (emails, names, message bodies).
- Registration stays explicit; flag any auto-discovery/entry-point mechanism.
