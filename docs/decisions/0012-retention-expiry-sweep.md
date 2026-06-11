# 0012. Retention-expiry sweep: anchored durations, report-only

- **Status:** accepted
- **Date:** 2026-06-11

## Context

`RetentionPolicy.duration` declares how long a retention duty lasts — and nothing consumes it (issue #48). A record retained past its declared window is never noticed, which is precisely the failure mode Art. 5(1)(e) (storage limitation) names: data kept "no longer than is necessary". The EDPB's 2025 coordinated enforcement action on erasure — 32 supervisory authorities auditing 764 controllers — singled out the lack of automated deletion capabilities as one of the two most persistent gaps. The honest mechanism is a sweep: find data whose retention window has lapsed and report it, audited.

Two forces shape the design. First, a `duration` is meaningless without a clock — *duration since what?* No anchor exists in the manifest today. Second, widened SemVer (ADR 0003): a sweep that deletes changes *what gets deleted* and is MAJOR; a sweep that reports is additive. And the wording rule is load-bearing here: whether a lapsed legal duty means the data may now be erased is a controller determination effaced must not make.

## Decision

### The anchor lives on `RetentionPolicy`

`RetentionPolicy` gains an optional `anchor: str | None = None`: the name of a datetime column **on the same table** as the annotated column, holding the instant the retention clock starts (an `invoiced_at`, a `closed_at`). The duty's duration and its clock belong to the same model — the policy is where the legal duty lives. Cross-table anchors are explicitly out of scope; they can be added compatibly later if a real schema demands one.

The SQLAlchemy adapter validates at `collect_data_map()` time: a named anchor column must exist on the table and be datetime-typed, else `ManifestError` before any sweep runs — the same fail-loudly-at-assembly direction as plan conflicts (ADR 0007).

A serialized field is a manifest format change: `MANIFEST_SCHEMA_VERSION` bumps and a forward-migration branch maps old payloads to `anchor=None`. Old manifests are never rejected.

### A policy is sweepable iff it has both `duration` and `anchor`

Columns whose policy carries a `duration` but no `anchor` (and rows whose anchor column is `NULL`) are reported as **indeterminate** — counted, never guessed. Indeterminacy is not an error: every existing manifest stays valid, and the report saying "these duties have a declared duration effaced cannot evaluate" is itself useful output. Eligibility ignores the erasure strategy: any `PiiSpec.retention` with both fields participates, `RETAIN` and otherwise — inclusive is safe because nothing is deleted.

### Report-only: `RetentionSweeper.sweep()`

A new `effaced/retention/` package ships `RetentionSweeper` and `RetentionReport` (frozen pydantic). `sweep(session, *, now=None)` is sync on the caller's `Session` (ADR 0006) and evaluates one cutoff instant for the whole run: per column, `cutoff = now - duration` is computed in Python, so the database sees a portable `anchor_column <= :cutoff` comparison against the bound table handle, scoped to subjects through the `TableAccessPlan` hop chains — the same correlated-subquery technique the erasure executor uses. The report lists, per (table, column): the policy's `reason`, the matched subject ids and row counts, and the indeterminate counts.

The sweep writes nothing and the **planner stays time-free**: `plan()` never consults `duration`, so an `ErasurePlan` is a pure function of the manifest, not of the wall clock. A delete mode — or an expiry-aware planner that treats a lapsed `RETAIN` as erasable — is a separate, future ADR and MAJOR under ADR 0003.

### Audit: `RETENTION_EXPIRED`, per subject, names and counts only

A new `AuditEventType.RETENTION_EXPIRED` member (additive). The sweep appends one event per subject with expired data: `subject_ref = subject_id`, payload `{table, column, rows}` — table/column names and counts, never values, never per-row anchor timestamps. Repeated sweeps re-emit for still-expired data; each run is evidence, the same direction as erasure re-runs (ADR 0009). A per-sweep summary event ("the sweep ran, found nothing") was considered and rejected: the trail records facts about data subjects, and scheduler liveness belongs to the application's own monitoring.

### What the report is for

The report names subjects; the operator's natural follow-up is `erase_subject(subject_id)` — with an honest caveat the docs must carry: erasure *retains* `RETAIN` columns by construction, so acting on a lapsed duty for a `RETAIN` column means changing the annotation (flip the strategy, drop the policy) and then erasing, or acting in the application directly. The sweep is the mechanism that notices; what a lapsed duty permits is the controller's determination, always.

## Consequences

- This lands as MINOR: a new package, an additive enum member, a schema bump with migration. The sweep's matching semantics (cutoff arithmetic, indeterminate handling, event shape) become MAJOR-protected once shipped.
- Annotations gain a second validation site: manifests that name nonexistent or non-datetime anchors fail at collection, not at sweep time.
- A controller wanting automated deletion does not get it here — deliberately. The future delete-mode ADR inherits a settled anchor model and an audit precedent, which is most of its surface.
- Property tests must pin: report-only (the sweep never mutates), indeterminate counting (no anchor ⇒ never matched as expired), and that `plan()` output is unaffected by any `duration`/`anchor` value.
