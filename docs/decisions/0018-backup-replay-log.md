# 0018. Backup replay of committed erasures

- **Status:** accepted
- **Date:** 2026-06-12

## Context

Restoring a database backup resurrects every subject erased after the backup
point — Art. 17 work silently undone by an ordinary operational act. effaced
already holds the record needed to repair this: the append-only audit trail
(ADR 0009) marks exactly which erasures were committed in the window between
the backup point and the restore. Issue #59 asks for a mechanism that replays
them. (django-gdpr-assist pioneered the idea; this ADR pins effaced's
semantics.)

Two facts shape the design. First, the restored database's own trail lost the
post-backup window — with the default same-database `DatabaseAuditSink`, the
restore rolled the trail back along with everything else. Replay therefore
must read a *surviving* record: an external sink, a replica, a pre-restore
dump of `effaced_audit_events`. Second, `erase_subject` re-runs are already
convergent no-ops (ADR 0009), so replay can delegate instead of inventing a
second erasure engine. What is pinned here is observable erasure behaviour;
under widened SemVer (ADR 0003) changing it later is MAJOR.

## Decision

### `ERASURE_LOCAL_COMPLETED` is the replay trigger, inclusive at the boundary

A restore resurrects **local** rows only; external systems are untouched by
it, and their erasures stand. The event that marks "local rows were
committed-erased and are now back" is `ERASURE_LOCAL_COMPLETED` — appended
exactly once per locally completed erasure, durable independently of the
caller's transaction (ADR 0006/0009). `ERASURE_COMPLETED` (the saga's
external-completion marker) is deliberately not the trigger.

The window is inclusive: an erasure whose `ERASURE_LOCAL_COMPLETED` has
`occurred_at >= backup_taken_at` is replayed. Whether a commit at exactly the
backup instant made it into the backup is unknowable; over-replay is a
convergent no-op, under-replay leaves resurrected PII. For the same reason
there is **no upper bound** (`restored_at` was considered and rejected):
replaying an erasure performed after the restore converges too, and callers
who want a bounded window can slice the input themselves.

### Derivation is pure; the indeterminate is surfaced, never guessed

`Replayer.plan(events, backup_taken_at)` is a pure function — no I/O, no
clock; the same event set in any order yields an equal `ReplayPlan`. A naive
(timezone-unaware) `backup_taken_at` raises `ConfigurationError` before
anything else: comparing it against the trail's UTC timestamps would be a
silent lie.

Per subject, looking only at events at/after the cutoff:

- any `ERASURE_LOCAL_COMPLETED` → **replayable**; the plan entry cites the
  latest qualifying event id and counts the completions, as evidence;
- else any `ERASURE_STEP_FAILED` → **failed-only**: the attempt rolled back,
  nothing was erased, the restore resurrected nothing erasure had removed;
- else any `ERASURE_REQUESTED` → **indeterminate**: an interrupted attempt
  whose outcome the trail does not show.

Failed-only and indeterminate subjects are listed on the plan for the
operator and never executed — the same counted-never-guessed posture as the
retention sweep (ADR 0012). All other event types are ignored by
classification.

### Replay delegates to `erase_subject`, append-first, fail-fast

`Replayer.replay(session, plan)` re-runs the erasure per replayable subject
through the wired `ErasurePlanner` — no second engine, so ADR 0007/0008/0009
semantics apply verbatim and every replayed erasure appends its full audit
sequence. Before each subject's re-run, one new additive
`AuditEventType.ERASURE_REPLAYED` event is appended (`subject_ref =
subject_id`, payload `{backup_taken_at, source_event_id, completions}` —
scalars only), under ADR 0015's ordering rule: the append happens **before**
any mutation, so if the sink is down nothing changes; duplicate events are
possible across crashes and re-runs, missing ones never.

Replay runs in the caller's open session and never commits (ADR 0006). It is
fail-fast: `erase_subject`'s contract forbids committing after it raises, so
continuing to the next subject in the same session would be unsound. On
failure the caller rolls back; audit events persist independently, and
re-running the replay converges — replays of replays are no-op successes.

### Refs are re-derived from restored data, never from the trail

The trail is PII-free by design and cannot carry external-system refs. It
does not need to: the restore resurrected exactly the columns refs derive
from. `Replayer` accepts an optional `refs_for(subject_id)` callable; the
default (`None`) replays local-only, which is correct because external
systems were not restored — their erasures stand, and re-enqueueing would be
convergent (`already_absent=True`) but opt-in noise.

### Input surface: plain events, plus a `ReplaySource` capability

Derivation takes a plain `Sequence[AuditEvent]`, agnostic to where the
surviving record lives. For the common case there is a standalone
`@runtime_checkable` `ReplaySource` protocol — `read_since(since)` returning
all subjects' events oldest-first — implemented concretely by
`DatabaseAuditSink`. `read_since` is deliberately **not** added to the
`AuditSink` protocol: a new non-defaultable protocol method would break
`isinstance` checks for every existing custom sink. A separate capability
protocol follows the `RectifyingResolver` precedent, looser still — a
dump-file loader can implement `ReplaySource` without being a sink at all.

`read_since` applies the same timezone guard as derivation: a naive
`since` raises `ConfigurationError`. On a timestamptz column a naive
bound silently shifts the window by the session offset — events drop out
of the read with no error, which on this path means resurrected PII never
replayed. Both ends of the pipeline refuse naive time.

### No new tables, no manifest change

Replay reads the existing trail and delegates to existing machinery.

## Consequences

- This lands as MINOR: a new `effaced.replay` package, an additive
  `AuditEventType` member, an additive concrete method on
  `DatabaseAuditSink`. Nothing changes for deployments that never call it.
  The trigger event, inclusive boundary, classification, append-first
  ordering, and local-only default are MAJOR-protected once shipped.
- The honest limitation must be documented loudly: the default same-database
  sink cannot serve its own restore window. Deployments that want replay
  must keep a surviving copy of the trail (external sink, replica, scheduled
  dump) — the mechanism consumes that record, it cannot conjure it.
- A subject's trail can now legitimately read `… LOCAL_COMPLETED · REPLAYED ·
  REQUESTED · …`; consumers must tolerate repeated sequences (already
  required by ADR 0009/0015's at-least-once posture).
- Replay re-applies erasures; it is a mechanism for converging after a
  restore, never a determination that the restore — or the deployment — is
  compliant.
