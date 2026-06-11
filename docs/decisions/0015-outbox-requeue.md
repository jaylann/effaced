# 0015. Supervised requeue of abandoned outbox entries

- **Status:** accepted
- **Date:** 2026-06-11

## Context

ADR 0010 made abandonment permanent: an entry that exhausts its retries (or fails non-retryably) is audited, surfaced via `list_abandoned()`, and never retried; remediation is out-of-band. Issue #50 anticipated the operator who eventually says "the Stripe outage is over — run the abandoned ones again" and deliberately deferred the ADR until demand. It is written now anyway: ADR 0013 reuses the outbox for rectification, and requeue semantics — the only mutation an operator can make to the outbox — must be settled coherently with the operation discriminator before either implementation starts, not retrofitted after.

The erasure pipeline's failure semantics are observable behaviour, so under widened SemVer (ADR 0003) what is pinned here is MAJOR to change later. An undue-delay framing also applies: an abandoned entry is an Art. 17 erasure the controller has not finished; a supervised path back into the queue is how the mechanism supports finishing it.

## Decision

### `Outbox.requeue(entry_ids)`: explicit ids, supervised by construction

The operator API is `Outbox.requeue(entry_ids) -> Sequence[OutboxEntry]`, taking explicit ids as produced by `list_abandoned()`. A blanket `requeue_abandoned(subject_id)` form was considered and rejected: the blanket call is one comprehension away in application code, and the primitive should force the operator to look at what they are about to re-run. Who may call it is out of library scope — this is a Python API; authorization is the application's.

### Transition: `ABANDONED → PENDING`, fresh budget, history in the trail

Only `ABANDONED` rows flip. A requeued entry becomes `PENDING` with `next_attempt_at = NULL` (due immediately), `attempts = 0`, and `last_error = NULL`: requeue asserts the operator believes the cause is fixed, so the entry gets the full retry budget rather than one borrowed attempt. The prior struggle is not lost — it moves into the requeue audit event's payload, where history belongs, instead of living on in row columns that now describe a fresh entry.

The whole call runs in one transaction that first locks the affected subjects' outbox rows `FOR UPDATE` ordered by `entry_id` — the same lock order as `mark_succeeded`'s completion check, so a requeue racing a concurrent runner serializes instead of deadlocking.

### Idempotent and skip-tolerant

Ids that are missing, or whose entry is no longer `ABANDONED` (a colleague requeued first; a generation already succeeded), are skipped — never errors. Calling `requeue` twice with the same ids is success; the return value reports the entries that actually flipped, in their post-requeue state. `entry_id` is stable across requeue, so the resolver-side idempotency key is unchanged and re-execution converges under the existing contract (`already_absent=True` is success).

### Audit: append-first `ERASURE_REQUEUED`

A new additive `AuditEventType.ERASURE_REQUEUED`; one event per flipped entry, `subject_ref = subject_id`, payload `{entry_id, resolver, prior_attempts, prior_error}` (exception class name only, never a message). The append happens **before** the status change, under ADR 0010's ordering rule: if the sink is down, nothing flips — duplicate events are possible on a crash between sink commit and outbox commit, missing ones never. Requeue is mechanically operation-agnostic; the event name follows the entry's `operation`, so `RECTIFICATION_REQUEUED` arrives with ADR 0013's implementation rather than being minted speculatively here.

### Completion: "permanent" narrows to "until supervised requeue"

ADR 0010's completion predicate — every entry for the subject `SUCCEEDED`, checked under lock — is unchanged. A requeued entry is `PENDING`, so the subject's completion simply waits again, and `ERASURE_COMPLETED` fires when the requeued entry lands, spanning generations exactly as re-runs of `erase_subject` already do. What this ADR amends in 0010 is one sentence: an `ABANDONED` entry blocks completion permanently *unless an operator requeues it*. Abandonment remains the terminal state the runner converges to; only a human path leads back out.

## Consequences

- This lands as MINOR: an additive operator API and an additive event type; nothing changes for deployments that never call it. The transition rule, attempts reset, and append-first ordering are MAJOR-protected once shipped.
- The outbox's operator surface is no longer read-only. `list_abandoned()`'s docstring and the saga runbook's remediation guidance ("clear the abandoned row, re-run `erase_subject`") describe the pre-requeue world and must be updated in the implementation PR (#90) — until then they remain accurate for the shipped surface.
- `attempts = 0` means a requeued entry's trail can show a second full abandonment after `max_attempts` fresh failures; each cycle is evidence, and an operator requeuing without fixing the cause will see exactly that in the trail.
- A subject's audit sequence can now read `… STEP_FAILED (abandoned) · REQUEUED · STEP_SUCCEEDED · COMPLETED`; consumers must not treat post-abandonment activity as an anomaly (the same at-least-once posture ADR 0010 already demands).
