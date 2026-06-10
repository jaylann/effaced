# 0010. Saga runner claim, retry, and completion semantics

- **Status:** accepted
- **Date:** 2026-06-11

## Context

ADR 0009 fixed what `erase_subject()` records and left `ERASURE_COMPLETED` "the saga runner's to emit" (issue #16). Executing outbox entries raises the questions this ADR pins: how concurrent runners claim without double-executing, what a retry schedule and a crashed runner's recovery look like, which failures retry versus abandon, and when a subject's erasure counts as complete. All of these are observable erasure behaviour, so under widened SemVer (ADR 0003) any later change is MAJOR.

## Decision

### One `next_attempt_at` gate, doing double duty

The outbox row carries a single nullable `next_attempt_at` (UTC): the earliest instant any runner may (re)claim the entry. `PENDING` rows carry `NULL` (due immediately); a claim stamps `now + lease` (the crash lease); a retryable failure stamps `now + backoff(attempts)` (the retry schedule); terminal rows carry `NULL` and are excluded by status. Status already disambiguates lease from backoff, so a second column would be redundant and would complicate the claim predicate.

### Claiming: `FOR UPDATE SKIP LOCKED`, attempts count claims

`Outbox.claim_batch` selects non-terminal rows whose gate has passed — `status IN (pending, failed, in_flight) AND (next_attempt_at IS NULL OR next_attempt_at <= now)` — oldest first (`enqueued_at`, `entry_id` tiebreak), locked with `FOR UPDATE SKIP LOCKED`, then flips them to `IN_FLIGHT` with `attempts + 1` in the same transaction. Including `in_flight` with an expired gate is what re-claims a dead runner's work. SQLite ignores `FOR UPDATE`; the no-double-claim guarantee is a property of dialects that honour it (Postgres), and the integration suite proves it there.

`attempts` increments **at claim time**, not at failure time: an entry whose execution crashes its runner before any bookkeeping would otherwise never count an attempt and be re-claimed forever. Counting the claim guarantees convergence to `ABANDONED` after `max_attempts` even for crash-looping entries; the visible consequence is that a crash-then-success trail can show `attempts > 1`.

### Failure taxonomy

| Outcome of the resolver call | Entry becomes | Audited |
|---|---|---|
| `ResolverErasure` (including `already_absent=True` — "already gone" is success) | `SUCCEEDED` | `ERASURE_STEP_SUCCEEDED`; `ERASURE_COMPLETED` when the subject's last entry lands |
| `ResolverError` — raised by the resolver (non-retryable by contract) or by the registry (unknown resolver name) | `ABANDONED` immediately | `ERASURE_STEP_FAILED` with `abandoned: true` |
| Any other `Exception`, attempts < `max_attempts` | `FAILED`, gate = `now + backoff(attempts)` | No event; the row's `last_error` carries the exception class name |
| Any other `Exception`, attempts ≥ `max_attempts` | `ABANDONED` | `ERASURE_STEP_FAILED` with `abandoned: true` |
| Runner crash / cancellation | Stays `IN_FLIGHT`; lease expiry re-claims it | Nothing yet — the retry converges (idempotent by `entry_id`) |

An unknown resolver name abandons the one entry rather than failing the whole run: a misconfigured deployment must not wedge the queue, and abandonment is already the loud path. Backoff is deterministic doubling (`BackoffPolicy`: base 30s, cap 1h, lease 5min by default) — no jitter; `SKIP LOCKED` already spreads concurrent runners.

### Audit ordering: append first, terminal outcomes only

The audit append happens **before** the status change (success and abandonment alike). If the sink is down, the entry stays `IN_FLIGHT` and the lease heals it — no recorded outcome can exist without its audit record; the cost is possible duplicate events on crash, never missing ones (the same evidence-preserving direction as ADR 0009). Intermediate retryable failures are deliberately not audited: the trail records outcomes, the outbox row (`last_error`, `attempts`) records the in-progress struggle.

### `ERASURE_COMPLETED`: every entry `SUCCEEDED`, checked under lock

When an entry lands `SUCCEEDED` and **all** outbox entries for its `subject_id` are now `SUCCEEDED`, the runner emits `ERASURE_COMPLETED` (empty payload; the per-step events carry the detail). The check runs inside the same transaction as the status update, after locking the subject's rows `FOR UPDATE` ordered by `entry_id` — two runners finishing a subject's last two entries serialize on identical lock order instead of deadlocking, so exactly one observes the transition. A crash between the sink's independent commit and the outbox commit can duplicate the event; it can never be missed.

An `ABANDONED` entry blocks completion **permanently**: the abandonment's `ERASURE_STEP_FAILED` is the subject's terminal record, and remediation is an operator action (fix the cause, clear the abandoned row, re-run `erase_subject` — see the saga-runner runbook). Re-runs of `erase_subject` enqueue fresh entries for the same subject (ADR 0009); the completion check naturally spans all generations.

## Consequences

- The claim query, the failure taxonomy, the backoff curve's observable schedule, and the completion condition are all MAJOR-protected erasure semantics from this point on.
- Audit events from the runner are at-least-once: tests (and consumers) must treat duplicates as re-execution evidence and assert on state, not exact event counts.
- A resolver call slower than the lease causes double execution — absorbed by the idempotency contract, but operators must size `BackoffPolicy.lease` above their slowest resolver.
- A subject with an abandoned entry never receives `ERASURE_COMPLETED`; monitoring `status = 'abandoned'` (or the `abandoned: true` audit payload) is part of operating the saga, documented in `docs/runbooks/saga-runner-wiring.md`.
