# 0018. Retention-only erasure: scheduled expiry, parked outbox entries, verified completion

- **Status:** accepted
- **Date:** 2026-06-12

## Context

The PII long tail of a real product includes data that no API can delete on demand: call recordings and transcripts at transcription/voice vendors, application logs in object storage with bucket-level lifecycle rules, exports sitting in a partner's retention window. The `Resolver` protocol assumes delete-on-demand (`erase_subject` either erases or fails); for these systems the only honest guarantee is *expiry* — "this will be gone by T, because the vendor's retention clock says so".

The non-negotiable that shapes everything here: **the trail must never record a deletion that did not happen.** An erasure that is "scheduled to expire by T" is a different audit fact than `ERASURE_COMPLETED`, and conflating them would be exactly the silent compliance lie effaced exists to prevent. Issue #107 asks for the pattern — a capability, its audit vocabulary, its Art. 15 surfacing, and enforcement of the horizon — not for any single vendor integration.

## Decision

### `RetentionOnlyResolver`: a capability sub-protocol whose erasure is a schedule

`Resolver` grows nothing. A new `@runtime_checkable` sub-protocol, `RetentionOnlyResolver(Resolver, Protocol)`, adds `async def schedule_erasure(ref) -> ResolverScheduledErasure` — the `RectifyingResolver` pattern (ADR 0013), the strictest reading of additive-only evolution. Call sites narrow with `isinstance`; the saga runner routes erase entries for such resolvers to `schedule_erasure` and never calls their `erase_subject`.

`ResolverScheduledErasure(resolver, expires_at, already_absent=False, detail=None)` mirrors `ResolverErasure`. Its convergence contract: scheduling a subject the system no longer holds — never held, already expired, or purged early — is success with `already_absent=True` (the analogue of erasure's `already_absent`); re-scheduling a subject already scheduled is success reporting the same-or-later horizon. Exactly one of the two facts holds (validator-enforced): either `already_absent=True` with no horizon, or a tz-aware `expires_at` — a schedule without a horizon is not an honest fact, and a horizon for data already gone is not a fact at all.

The structurally-required `erase_subject` on a retention-only implementation **raises `ResolverError`**. Returning a fabricated success would record a deletion that did not happen; raising is the same loud taxonomy as a rectify entry hitting a non-rectifying resolver (ADR 0013). A vendor that can delete some data on demand but only expire the rest is modeled as **two resolvers with two ref kinds** — ADR 0008's kind==name routing already supports that split, and a per-call mode discriminator would leak into the outbox and the protocol for no gain.

No new `OutboxOperation`: the request is still an Art. 17 erasure, entries stay `ERASE`, completion grouping per (subject, operation) is untouched, and the planner needs no changes — a retention-only resolver registers and routes like any other.

### Enforcement: park until the horizon, then verify

Recording a horizon is not enforcing it. The outbox already owns the time gate (`next_attempt_at` is "earliest instant any runner may claim"), so the horizon rides it:

- New `OutboxStatus.SCHEDULED` member, claimable like `PENDING`/`FAILED`. The `status` column is a plain string precisely so the enum can grow additively; no DDL changes.
- When a schedule reports a future horizon, the runner appends the audit event **first** (append-before-flip, ADR 0010's ordering rule), then `Outbox.mark_scheduled(entry, resume_at=...)` parks the entry: `SCHEDULED`, `next_attempt_at = max(expires_at, now + backoff)` (the clamp prevents hot-looping on a stale or past horizon), `attempts = 0`, `last_error = NULL`. The fresh budget is the ADR 0015 requeue precedent — the prior struggle moves into the audit event (`prior_attempts`), where history belongs; the entry that wakes after the horizon is a fresh verification with the full retry budget.
- After the horizon, `claim_batch` re-claims the entry and the **same code path** re-runs `schedule_erasure`. Vendor purged → `already_absent=True` → the entry succeeds, *verified*. Vendor reports a new horizon → the entry re-parks and is loudly re-audited; each slipped horizon is evidence, not noise.
- Failure taxonomy is ADR 0010 verbatim: transient errors retry on backoff, `ResolverError` abandons with the step-failed event.

A consequence pinned as intended: a vendor that forever reports fresh horizons never abandons (the park resets the budget) — it accumulates one scheduled event per slip and holds the subject's erasure open. Loud and inspectable beats a terminal state that pretends the question is settled.

The retention sweeper stays exactly as ADR 0012 left it: database-only, report-only. The *saga* is the enforcement clock for external horizons; surfacing them in `RetentionReport` is a separate, future decision.

### Audit vocabulary: a schedule is never a completion

One additive `AuditEventType` member: `ERASURE_EXPIRY_SCHEDULED`, payload `{target, external: true, expires_at, prior_attempts}`. The `expires_at` ISO-8601 instant is the *substance* of the fact — "scheduled to expire by T" is meaningless without T — and it is a vendor retention policy instant, not subject data; this does not soften ADR 0012's exclusion of per-row anchor timestamps, which are values from user rows.

`ERASURE_COMPLETED` keeps its exact published meaning: every erase entry for the subject `SUCCEEDED`. A `SCHEDULED` entry blocks completion through the existing all-succeeded predicate — no amendment needed. When verification finds the data gone, the step success is recorded as `ERASURE_STEP_SUCCEEDED` with `{target, external: true, verified_expiry: true, already_absent: true, attempts}` and deliberately **no** `strategy: "delete"` key: effaced deleted nothing; it verified expiry. Only then can `ERASURE_COMPLETED` fire. Alternatives rejected: a second completion event ("completed-with-pending-expiry") splits "done" into two facts every trail reader must union; stuffing pending-expiry counts into `ERASURE_COMPLETED`'s payload makes the event mean "maybe".

### Art. 15: the horizon is exportable metadata

`ExportRecord` gains `expires_at: AwareDatetime | None = None` — "the instant by which this value is guaranteed to expire at its source, when on-demand erasure there is unavailable". Retention-only resolvers stamp it (typically alongside a `retention_reason` naming the vendor's policy); local records and ordinary resolvers leave it `None`. No `MANIFEST_SCHEMA_VERSION` bump: `migrate()` governs serialized *data-map* payloads, while `ExportBundle` is produce-only — nothing in effaced parses bundles back — so a defaulted additive field is MINOR. That precedent is now pinned here, and so is its boundary: `ExportBundle` validates with `extra="forbid"`, so the day effaced ships a bundle *reader*, this exemption ends — bundle-shape changes from then on need versioning and a forward path of their own.

## Consequences

- This lands as MINOR (vacuous additivity, the ADR 0013 argument): no `RetentionOnlyResolver` exists until someone implements one, the enum members are additive, and `ExportRecord.expires_at` defaults to `None`. Everything above is MAJOR-protected once shipped.
- For subjects touching retention-only systems, erasure latency becomes the vendor's retention window. `status_counts()[SCHEDULED]` is the operator signal for "pending vendor expiry, not a fault" — the saga runbook says so.
- The conformance suite grows a scheduled-erasure section (convergence, absent-subject success, `erase_subject` raising, horizon-stamped exports); the ordinary erase-path tests skip for retention-only resolvers, whose `erase_subject` raises by contract. `InMemoryRetentionOnlyResolver` is the reference implementation.
- `requeue` (ADR 0015) is unaffected: it consumes only `ABANDONED` entries, and a parked entry is not abandoned.
- Follow-ups deliberately not built here: an `Outbox.list_scheduled()` operator read surface; resolver-aware horizons in the retention sweep (own ADR — the sweeper stays DB-only today); applying the pattern in a real vendor package (#60/#61/#70).
