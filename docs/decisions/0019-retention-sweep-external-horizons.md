# 0019. External retention horizons stay out of the sweep report

- **Status:** accepted
- **Date:** 2026-06-12

## Context

ADR 0018 (retention-only erasure) parks an erase entry a vendor can only *expire*
as `OutboxStatus.SCHEDULED` until its horizon, then re-verifies. Those external
horizons live in two places: the outbox row (`next_attempt_at` is the horizon
gate) and the audit trail (`ERASURE_EXPIRY_SCHEDULED`, carrying `expires_at`).
ADR 0018 closed by deferring two follow-ups to their own decisions — one of them
verbatim: "resolver-aware horizons in the retention sweep (own ADR — the sweeper
stays DB-only today)" (issue #114).

The question that follow-up poses: `RetentionReport` is the one place an operator
looks for "what is retained until when", yet it is database-only (ADR 0012) and
cannot see external horizons. Should `RetentionSweeper`/`RetentionReport` grow
awareness of scheduled external expiries — e.g. an entries section sourced from
`SCHEDULED` outbox rows — or should that stay a separate read surface?

This decision is a deliberate *no* to merging them, recorded so the omission reads
as a choice rather than an oversight. It is settled now because its sibling
follow-up — `Outbox.list_scheduled()` (issue #113) — ships the separate read
surface in the same change, which is precisely what makes the separation tenable.

## Decision

**The retention sweeper stays exactly as ADR 0012 left it: database-only,
report-only, `SELECT`-only, storage-agnostic, taking no resolver or outbox
collaborator. External horizons surface through the outbox read surface
(`Outbox.list_scheduled()`, #113) and the `ERASURE_EXPIRY_SCHEDULED` audit trail —
not through `RetentionReport`.**

Two surfaces, each with one honest meaning:

- `RetentionReport` answers "what does *this database* still hold past its declared
  duty" — rows whose anchor column has lapsed its `duration`, attributed to
  subjects (ADR 0012). The follow-up is the controller's: re-annotate, then
  `erase_subject`.
- `list_scheduled()` / the audit trail answer "what is an *external* system parked
  to expire, and until when" — `SCHEDULED` entries ordered by their horizon. The
  saga is the enforcement clock; the outbox is its inspection surface.

### Why not fold external horizons into the sweep

- **Constructor surface.** The sweeper takes `(data_map, graph, metadata,
  audit_sink)` and nothing saga-shaped. Teaching it about scheduled expiries means
  injecting an outbox (or resolver) and coupling a pure database read to saga
  state — a widening of a stable collaborator surface, and MAJOR-adjacent care, for
  a join an operator can already do across two clear surfaces.
- **Provenance.** `RETENTION_EXPIRED` is a per-row anchor lapse computed from user
  rows (ADR 0012, which deliberately excludes per-row anchor timestamps from
  payloads as subject data). `ERASURE_EXPIRY_SCHEDULED` carries a *vendor policy*
  instant, explicitly not subject data (ADR 0018). They are different facts with
  different provenance; one report listing both invites a reader to treat a vendor
  horizon as a per-row retention duty, or vice versa.
- **Time-free planner stays time-free (ADR 0007).** Nothing here touches `plan()`;
  the sweep remains the only time-aware read, and it gains no new inputs.

## Consequences

- **No code lands for this ADR.** `RetentionSweeper` and `RetentionReport` are
  unchanged; issue #114 closes as decided-not-built, with this record as the
  rationale. The work that *does* land alongside it is #113's `list_scheduled()`.
- Re-openable on concrete demand: if operators show they genuinely need a single
  unified "retained until when" view spanning local rows and external horizons, a
  later ADR can supersede this — but the default is two honest surfaces, not one
  blurred one.
- No `MANIFEST_SCHEMA_VERSION` bump, no enum or public-API change — a decision doc
  only.

See also: ADR 0012 (retention-expiry sweep, database-only), ADR 0018
(retention-only erasure), issues #113 and #114.
