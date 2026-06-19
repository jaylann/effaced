# 0028. Audit trail tamper-evidence via a hash chain

- **Status:** accepted
- **Date:** 2026-06-19

## Context

`effaced_audit_events` is append-only **by construction** (ADR 0009): no code
path updates or deletes a row, and the `AuditSink` protocol exposes no surface
to do so. That construction binds *effaced's own writers*. It says nothing
about a DBA with raw SQL, a second application sharing the database, or a
restore that silently rewinds the table — any of whom can rewrite a recorded
row, and the in-band trail would never show it. The optional Postgres trigger
in `docs/runbooks/append-only-audit-hardening.md` rejects `UPDATE`/`DELETE`
at the database, but a table owner or superuser can drop the trigger, and many
deployments cannot install one at all.

The trail is the library's evidence of record (Art. 30). Evidence that can be
edited without leaving a mark is weak evidence. Issue (safety item A5) asks for
an **out-of-band, in-row** way to *detect* that recorded history was modified —
not a new write path, not a guarantee that modification is impossible.

What is pinned here is observable audit behaviour; under widened SemVer
(ADR 0003) changing it later is MAJOR.

## Decision

### A per-event hash chain, computed at the sink boundary

Each stored event carries the SHA-256 hash of its own load-bearing content
**chained to the prior event's hash**: `event_hash = SHA256(canonical(event)
|| prior_hash)`. Mutating any field of event *K* changes *K*'s recomputed
hash, which breaks the link every later event committed to — so a single
out-of-band edit is detectable at *K*, and re-deriving the chain from scratch
localizes the *first* break. This is the standard tamper-**evidence**
construction (the same shape as a git commit chain or a transparency log's
left spine); it makes modification *detectable*, it does not make it
*impossible*.

The chain is computed at the **sink boundary**, not in the domain model. The
frozen `AuditEvent` (ADR 0009) stays untouched: it is the caller-facing fact,
and a hash is an artifact of *storage*, not of *what happened*. `AuditEvent`
carries no `prior_hash`/`event_hash`, so every existing producer, the export
bundle, and the PII-free payload contract are all unchanged. The
`DatabaseAuditSink.append` path is where ordering and durability already live
(its own short transaction, ADR 0006), so it is the one correct place to read
the prior link and write the new one.

### Two nullable columns — additive under ADR 0021

`effaced_audit_events` gains `prior_hash` and `event_hash`, both
`String(64)` (hex SHA-256) and **nullable**. Nullable means the caller's
`alembic revision --autogenerate` emits a plain additive `ALTER TABLE ADD
COLUMN` that backfills existing rows with `NULL` in place — no server default
needed (`test_no_server_defaults_*` keeps its single outbox carve-out). This
is an additive MINOR owned-table change (ADR 0021); the changelog instructs
callers to re-run autogenerate after upgrading.

### Legacy NULL-hash rows verify as an "unchained prefix", never a failure

A trail written before this release — or by a custom sink that does not
compute the chain — holds `NULL` in both columns. Those rows are an
**unchained prefix**: verification skips them (they were never chained, so
there is nothing to check) and begins enforcing at the first row that carries
an `event_hash`. A `NULL` hash is *absence of evidence*, not *evidence of
tampering*; treating an un-upgraded trail as "broken" would be a false alarm
that trains operators to ignore the real signal. The chain need not be
contiguous from the table's first-ever row to be meaningful from the point it
starts.

A row whose `event_hash` is present must chain correctly to its predecessor's
`event_hash` (or to `NULL`/absent for the first chained row, recorded as its
`prior_hash`). A present hash that does not recompute is the break.

### Verification is a standalone object, never an `AuditSink` method

`AuditChainVerifier` reads the trail through the **existing read surface** and
recomputes the chain, returning a `ChainVerification` verdict (`verified:
bool`, `first_broken_event_id: UUID | None`). It is **not** a method on
`AuditSink`: the protocol is append-only by construction and additively-only
(ADR 0009/gdpr-semantics) — a new non-defaultable method would break
`isinstance` for every existing custom sink. This follows the `ReplaySource`
precedent (ADR 0023): a verification *capability* is a separate object reading
via the public surface, not a widening of the sink contract. Verification is
pure read-back; it writes nothing and (deliberately) appends no audit event of
its own — a verifier that mutated the trail it verifies would be incoherent.

`compute_event_hash(event, prior_hash)` is a pure, deterministic,
order-sensitive function (no I/O, no clock). It canonical-serializes the
event's load-bearing fields — `event_id`, `event_type`, `subject_ref`,
`occurred_at`, `payload` — together with `prior_hash` into a stable JSON
encoding (sorted keys, normalized separators, the timestamp as a canonical
ISO-8601 string, `payload` keys sorted) and SHA-256-hex-digests it. The
encoding is documented and frozen: for these all-`str`/`StrEnum`/scalar
fields JSON and python serialization coincide, so a single canonical encoding
suffices and is what both the sink and the verifier call — they never fork it.

### Concurrency: the append transaction is the serialization point

`append` reads the most-recent chained row's `event_hash` (highest
`occurred_at`, `event_id` tiebreak — the same total order `read` uses) and
writes the new `prior_hash`/`event_hash` inside its existing per-append
transaction. Two appends racing for the same predecessor is the one hazard;
it is resolved exactly where ordering already lives. The chain is a
*detection* artifact, not a write-serialization mechanism: under genuinely
concurrent appends two rows may legitimately cite the same prior link, which
the verifier reports as a fork at that point — surfaced, never silently
healed. Deployments needing a strictly linear chain serialize appends
themselves (a single audit writer, or the optional advisory lock noted in the
runbook); the common single-writer erasure/consent path is already linear.

### No manifest change, no new table

The chain lives in two columns on an existing owned table and is verified by
reading the existing trail. Manifests are serialized JSON, never DB rows
(ADR 0021) — `MANIFEST_SCHEMA_VERSION` is untouched.

## Consequences

- This lands as **MINOR**: two nullable columns on an owned table, a new
  `compute_event_hash` function, and two new standalone objects
  (`AuditChainVerifier`, `ChainVerification`). No protocol changes, no
  signature changes, nothing changes for deployments that never verify. The
  canonical encoding, the chained-at-append computation, the unchained-prefix
  rule, and the standalone-verifier shape are MAJOR-protected once shipped.
- The honest framing, stated loudly everywhere this is documented: the chain
  **detects out-of-band modification of recorded rows**. It does **not**
  "prevent tampering", "guarantee integrity", or make the trail immutable — a
  writer with table access can recompute the whole chain forward from the row
  they edit. Detection raises the cost and visibility of doing so; combine it
  with the append-only trigger and restricted grants (runbook) for defence in
  depth. It is a mechanism, never a determination that a deployment is secure
  or compliant.
- Custom sinks are unaffected: they may leave both columns `NULL` and their
  trails verify as a fully-unchained prefix (vacuously `verified=True`). A
  custom sink that *wants* tamper-evidence computes the chain the same way
  `DatabaseAuditSink` does — `compute_event_hash` is public for exactly that.
- A subject's trail and the export bundle are byte-for-byte unchanged: the
  hash columns are storage artifacts, never surfaced as `AuditEvent` fields.
