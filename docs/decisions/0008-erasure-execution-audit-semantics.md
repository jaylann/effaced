# 0008. Erasure execution: audit semantics, ref fan-out, re-run behaviour

- **Status:** accepted
- **Date:** 2026-06-10

## Context

ADR 0007 fixed what an erasure *plan* contains; `erase_subject()` (issue #15) executes it. Execution raises three questions the plan cannot answer: what the audit trail records when an erasure succeeds, fails mid-transaction, or is re-run; which outbox entries the external steps produce when the caller supplies multiple `SubjectRef`s; and what "idempotent re-run" means for rows that survive erasure by design. All three decide observable erasure behaviour, so under widened SemVer (ADR 0003) any later change is MAJOR.

## Decision

### Audit event sequence (per `erase_subject` call)

All events carry `subject_ref = subject_id`, a fresh `event_id`, `occurred_at = now (UTC)`, and scalar-only payloads — exception **class names** only, never messages (database errors embed row values; the trail stays PII-free).

1. Validation failures (missing wiring, plan conflicts, external steps with empty refs) raise **before any event** — the trail starts when execution starts.
2. `ERASURE_REQUESTED` before the first step: `{local_steps, external_steps, refs}`. With the default `DatabaseAuditSink` (each append commits in its own short transaction, ADR 0006) this is the durable record of the attempt, surviving any later rollback.
3. One `ERASURE_STEP_SUCCEEDED` per local step, **including `RETAIN` steps**: `{target, strategy, rows}`. The RETAIN event *is* the auditable retention decision ADR 0007 promises; per-table counts live here, totals in the completion event.
4. On the first failure (step or enqueue): `ERASURE_STEP_FAILED` `{target, strategy, error}` (`target="outbox"`, `strategy="enqueue"` for enqueue failures), then the original exception re-raises. The caller must roll back and not commit; the failure event persists independently.
5. `ERASURE_LOCAL_COMPLETED` after the outbox enqueue, last: totals `{deleted, anonymized, retained, enqueued}`.
6. `ERASURE_COMPLETED` is the saga runner's to emit (issue #16), after external steps finish.

A completion event for a transaction the caller then rolls back is possible and deliberate — the same evidence-preserving direction as `ConsentLedger`. A failing sink aborts the erasure immediately: no erasure work proceeds unaudited.

### Outbox fan-out: every resolver gets every ref

One `PENDING` entry per (external step, ref) pair, fresh `entry_id`s. A `SubjectRef.kind` (`"stripe_customer"`, `"email"`) is not a resolver name, so any routing heuristic risks silently skipping external erasure — the one unrecoverable failure. Resolver idempotency (`already_absent=True` is success) makes over-asking converge. External steps planned with **no refs** raise `ConfigurationError` before anything executes: the registry is the auditable declaration that PII lives there, and an erasure that cannot address it must not look like success. Selective routing can arrive later as an optional, default-accepting `Resolver` member (additive, per the protocol stability rule).

### Re-run semantics ("no-op success")

Re-running for an already-erased subject succeeds: row-deleting tables report zero; surviving rows (anonymized in place, retained) still match by subject id and are reported — and re-anonymized with fresh surrogates — again; external work re-enqueues under fresh idempotency keys and converges at the resolvers. Skipping the enqueue when the local phase found nothing would silently skip external-only data.

### Counting

A surviving row anonymized in some columns and retained in others counts in both `anonymized[table]` and `retained[table]` — both statements about the row are true.

## Consequences

- The trail for one local erasure is `REQUESTED · n×STEP_SUCCEEDED · (STEP_FAILED | LOCAL_COMPLETED)`; integration tests pin the sequence and the rollback-survival of the attempt record.
- Re-runs append a full second sequence; each attempt is evidence. Surrogate values churn on re-runs (anonymization is not byte-stable, only state-stable).
- Duplicate resolver calls are possible by design and absorbed by the idempotency contract; the saga runner must not treat a duplicate `already_absent` as an anomaly.
- Callers integrating subjects with no external presence still pass a lookup ref (e.g. `kind="email"`); resolvers answer `already_absent=True`.
