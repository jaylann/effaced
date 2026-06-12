---
paths: ["packages/effaced/src/**", "packages/effaced-stripe/src/**"]
---

# GDPR semantics — the rules that make this library trustworthy

## Widened SemVer (the project's #1 rule)
"Breaking" includes **behaviour**: any change to *what gets deleted or exported* is MAJOR, even with identical signatures. Silently changing compliance behaviour is the worst possible failure. If a change alters erasure/export results for any input, it must:
1. be declared in the PR body's "Erasure/export semantics" section,
2. carry the `breaking` label,
3. use `feat!:`/`BREAKING CHANGE:` so release-please cuts a major.

## Manifest format
- Any serialized-format change bumps `MANIFEST_SCHEMA_VERSION` (in `effaced/manifest/migration.py`) and adds an explicit forward-migration branch.
- Old manifests are migrated forward, **never rejected**. Newer-than-known manifests fail loudly with upgrade guidance.
- Adding a field behind a forward migration is MINOR (ADR 0012 precedent: `RetentionPolicy.anchor`); removing or renaming serialized fields is MAJOR.
- Removing/renaming enum members (`PiiCategory`, `LegalBasis`, `ErasureStrategy`, `AuditEventType`) is a format change → MAJOR.

## Retention is sacred
- A field declared `ErasureStrategy.RETAIN` must never be deleted by any code path. The planner raises `RetentionViolationError` rather than guessing.
- `RETAIN` always requires a `RetentionPolicy` naming the legal reason (validator-enforced — keep it that way).
- Retention decisions are recorded in the audit trail, not silently applied.
- In plans, `RETAIN` columns appear only in `RETAIN` steps; row deletion requires a fully-PII-owned table whose annotated columns are all `DELETE` (ADR 0007 — changing those semantics is MAJOR).

## Audit trail
- Append-only **by construction**: no update/delete methods on sinks, ever. Adding one is an automatic review blocker.
- Events carry references and small scalars — never rich PII (no emails, names, message bodies in payloads).
- Every consent change, export, and erasure outcome (including failures and abandonment) produces an event. Nothing is silently dropped.

## Resolvers & saga
- `Resolver` and `AuditSink` protocols are public API with the strictest stability promise: extend **additively only**. The pattern for capability growth is a separate `@runtime_checkable class XResolver(Resolver, Protocol)` in `resolvers/` + `isinstance` narrowing at call sites — the base protocol stays literally untouched (`RectifyingResolver` and `RetentionOnlyResolver` are the precedents). Never change existing signatures.
- Idempotency contract: erasing a subject that's already gone is SUCCESS (`already_absent=True`), never an error; re-applying a correction the system already reflects is SUCCESS (`already_consistent=True`) — the convergence analogue. Saga retries depend on both.
- Outbox entries are enqueued in the SAME transaction as the local phase. Anything else reintroduces the half-erased/half-rectified-state bug this library exists to prevent.
- Terminal saga outcomes are always audited (`ERASURE_STEP_*` / `RECTIFICATION_STEP_*` with `abandoned: true` on abandonment); abandonment is never silent. Completion is per (subject, operation): `ERASURE_COMPLETED` requires every *erase* entry for the subject `SUCCEEDED`, `RECTIFICATION_COMPLETED` every *rectify* entry — an abandoned entry blocks its own operation's completion permanently, never the other's. Claim, retry/backoff, and completion semantics are ADR 0010 (amended by ADR 0013/0018) — changing them is MAJOR.
- Retention-only erasure (ADR 0018): a `RetentionOnlyResolver`'s schedule is a **distinct audit fact** (`ERASURE_EXPIRY_SCHEDULED` with the horizon), never a step success — `ERASURE_COMPLETED` fires only after a post-horizon claim verifies the data gone (`already_absent=True`, step payload carries `verified_expiry` and never `strategy: delete`). Never record a deletion that did not happen; a vendor that keeps slipping its horizon re-parks loudly instead of completing or abandoning.
- A rectify entry's corrections live in the outbox row's `payload` — real PII: cleared on every terminal transition (SUCCEEDED and ABANDONED alike), kept on FAILED for the retry, never mirrored into any audit event.
- Erasure strategy does not gate rectification: `RETAIN` and `ANONYMIZE` columns of a corrected category are rewritten too (ADR 0013) — do not "fix" this asymmetry.
- Registration stays explicit — no auto-discovery, no entry-point magic. The registry is an auditable "where is my PII" declaration.

## Wording discipline (load-bearing, legally)
- effaced ships *mechanisms*, never compliance *determinations*. No docs, docstring, log message, or marketing copy may say or imply effaced "makes you GDPR-compliant".
- The honest framing: "correct, tested machinery to implement Articles 15, 17, 7, and 30 — and an auditable record that you did."
