# 0014. Restriction of processing: an append-only flag ledger, never enforcement

- **Status:** accepted
- **Date:** 2026-06-11

## Context

Art. 18(1) gives the data subject the right to restrict processing — typically while an accuracy dispute (Art. 16) or an objection is open. Restricted data is *kept but not used*: storage remains lawful, and processing beyond storage requires consent or establishment/exercise/defence of legal claims (Art. 18(2)); the subject must be informed before a restriction is lifted (Art. 18(3)). Recital 67 names the methods, and the first one is exactly what a library can ship: *flagging in the system* — "the fact that the processing of personal data is restricted should be clearly indicated in the system".

Issue #47 frames the mechanism as consent-adjacent, and it is: a per-subject (optionally per-purpose) restriction state with an audited history and a query surface applications consult before processing. *Which* processing must stop, and whether a given operation falls under an Art. 18(2) exception, is a controller determination effaced must never make. The derivation and tie-breaking rules below are observable behaviour, so under widened SemVer (ADR 0003) they must be pinned before code.

## Decision

### `effaced/restriction/`, mirroring `consent/`

A new package with `RestrictionRecord(subject_id, purpose: str | None, restricted: bool, reason: str | None, recorded_at, source)` — `purpose=None` means all processing — and `RestrictionLedger(restriction_records, audit_sink)` with `record(session, record)`, `status(session, subject_id, purpose=None)`, and `history(session, subject_id)`. The contracts are the `ConsentLedger`'s, verbatim: records are immutable events written through the caller's session (ADR 0006); every `record()` mirrors an audit event through the constructor's sink; a failing sink raises before the caller can commit, so no restriction change persists unaudited, while the converse (an event for a write the caller rolls back) is the deliberate, evidence-preserving direction. Storage is a new `effaced_restriction_records` table in `bind_tables()` (additive).

### Status derivation: latest per scope, restricted wins

Status is derived, never stored. The effective answer for (subject, purpose) considers two events: the latest global record (`purpose IS NULL`) and the latest record for that purpose — the subject is restricted if **either** restricts. A purpose-level lift therefore cannot undo a global restriction; lift globally instead. Exact-timestamp ties resolve to the restricting record — when the order of a placement and a lift is unknowable, effaced assumes the subject is restricted, the same protective direction as consent's withdrawn-wins tie-break. There is no transition validation: lifting a restriction that was never placed simply appends — events are evidence, not a state machine.

### Audit: `RESTRICTION_PLACED` / `RESTRICTION_LIFTED`

Two additive `AuditEventType` members, one event per `record()` call, `subject_ref = subject_id`. The payload is `{purpose}` for purpose-scoped records and `{scope: "all"}` for global ones — **never `reason` or `source`**: free-text fields are PII-bearing by nature, the same discipline that keeps `source` out of consent payloads. The full record, `reason` included, lives in `history()` — which together with the `RESTRICTION_LIFTED` event is the mechanical substrate for the Art. 18(3) duty to inform before lifting; the informing itself is the controller's process.

### Explicitly no enforcement

No effaced engine consults the ledger, and nothing intercepts queries — Recital 67's "flag clearly indicated in the system" is precisely the mechanism shipped, and `status()` is the surface applications check before processing. The interplay with the other mechanisms is pinned both ways:

- **Export still runs** for a restricted subject. Art. 15 access reads storage, which Art. 18(2) permits — and the subject asked.
- **Erasure still runs.** Restriction is commonly what a subject chose *instead of* erasure (Art. 18(1)(b)); whether an erasure request overrides a standing restriction is a determination only the controller can make, and effaced refusing (or warning) would be making it. Both states sit side by side in the audit trail.

## Consequences

- This lands as MINOR: a new package, a new effaced-owned table, two additive enum members. The derivation rule (global-or-purpose, restricted-wins ties) is MAJOR-protected once shipped.
- The flag is only as good as the application's discipline in consulting `status()` — effaced ships the indication, not the suppression. The docs must say this plainly rather than imply Recital 67's other methods (moving or locking the data) are covered.
- Purpose strings are the application's vocabulary, as in consent; effaced does not validate them against a registry, so a typo'd purpose silently scopes its own restriction. The completeness linter does not cover this; the docs carry the warning.
- Property tests must pin: ties resolve to restricted, a global restriction shadows any purpose-level lift, status with no records is unrestricted, and payloads never contain `reason`/`source`.
