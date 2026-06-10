# 0009. Erasure execution: audit semantics and re-run behaviour

- **Status:** accepted
- **Date:** 2026-06-10

## Context

ADR 0007 fixed what an erasure *plan* contains and ADR 0008 fixed how refs route to resolvers; `erase_subject()` (issue #15) executes the plan. Execution raises two questions neither answers: what the audit trail records when an erasure succeeds, fails mid-transaction, or is re-run; and what "idempotent re-run" means for rows that survive erasure by design. Both decide observable erasure behaviour, so under widened SemVer (ADR 0003) any later change is MAJOR.

## Decision

### Audit event sequence (per `erase_subject` call)

All events carry `subject_ref = subject_id`, a fresh `event_id`, `occurred_at = now (UTC)`, and scalar-only payloads — exception **class names** only, never messages (database errors embed row values; the trail stays PII-free).

1. Validation failures (missing wiring, plan conflicts, a ref kind matching no registered resolver) raise **before any event** — a malformed call never became a data-subject request, so it deliberately leaves no audit trace (same rule as the Exporter).
2. `ERASURE_REQUESTED` before the first step: `{local_steps, external_steps, refs}`. With the default `DatabaseAuditSink` (each append commits in its own short transaction, ADR 0006) this is the durable record of the attempt, surviving any later rollback.
3. One `ERASURE_STEP_SUCCEEDED` per local step, **including `RETAIN` steps**: `{target, strategy, rows}`. The RETAIN event *is* the auditable retention decision ADR 0007 promises; per-table counts live here, totals in the completion event. The append is part of the step: a step whose outcome cannot be recorded counts as failed.
4. On the first failure (step, its success-append, or enqueue): `ERASURE_STEP_FAILED` `{target, strategy, error}` (`target="outbox"`, `strategy="enqueue"` for enqueue failures), then the original exception re-raises. The caller must roll back and not commit; the failure event persists independently. If the sink itself is down, the failure-append fails too and the erasure aborts with `ERASURE_REQUESTED` as the abandonment marker — no erasure work ever proceeds unaudited.
5. `ERASURE_LOCAL_COMPLETED` after the outbox enqueue, last: totals `{deleted, anonymized, retained, enqueued, skipped_resolvers}` (comma-joined names, as in `EXPORT_COMPLETED`).
6. `ERASURE_COMPLETED` is the saga runner's to emit (issue #16), after external steps finish.

A completion event for a transaction the caller then rolls back is possible and deliberate — the same evidence-preserving direction as `ConsentLedger`.

### Outbox fan-out: ADR 0008 routing, enqueued durably in the caller's transaction

One `PENDING` entry per (external step, ref) pair where `ref.kind == resolver.name`, fresh `entry_id`s. A ref kind matching no registered resolver raises `ResolverError` before any work or audit event; a registered resolver with no matching ref is skipped — a complete answer ("the subject has no identity in that system"), recorded in `ERASURE_LOCAL_COMPLETED.skipped_resolvers` and absent from `ErasureResult.enqueued_external`. Entries write through the caller's session: rows and outbox entries commit or roll back as one unit.

### Re-run semantics ("no-op success")

Re-running for an already-erased subject succeeds: row-deleting tables report zero; surviving rows (anonymized in place, retained) still match by subject id and are reported — and re-anonymized with fresh surrogates — again; matched external work re-enqueues under fresh idempotency keys and converges at the resolvers (`already_absent=True` is success).

### Counting

A surviving row anonymized in some columns and retained in others counts in both `anonymized[table]` and `retained[table]` — both statements about the row are true.

## Consequences

- The trail for one local erasure is `REQUESTED · n×STEP_SUCCEEDED · (STEP_FAILED | LOCAL_COMPLETED)`; integration tests pin the sequence and the rollback-survival of the attempt record.
- Re-runs append a full second sequence; each attempt is evidence. Surrogate values churn on re-runs (anonymization is not byte-stable, only state-stable).
- Duplicate resolver calls across re-runs are possible by design and absorbed by the idempotency contract; the saga runner must not treat a duplicate `already_absent` as an anomaly.
- An erasure run with refs covering no resolver enqueues nothing and says so in the trail; callers who expect external erasure must pass refs whose kinds name their resolvers (ADR 0008).
