# 0027. Post-erasure external verification

- **Status:** accepted
- **Date:** 2026-06-19

## Context

The saga runner marks an erase entry `SUCCEEDED` the moment a resolver's
`erase_subject` returns a `ResolverErasure` (`saga/runner.py::_succeed`). That
return is the resolver's own report — "I deleted it" (`already_absent=False`)
or "it was already gone" (`already_absent=True`). The runner never
independently re-queries the external system to confirm the record is in fact
absent. For the *local* surface there is already an independent read-back:
`ErasureVerifier` (`adapters/sqlalchemy/erasure_verifier.py`) re-derives the
ADR 0007 classification and SELECT-counts every row-deleted table, emitting
`ERASURE_VERIFIED` / `ERASURE_VERIFICATION_FAILED`. There is no external
analogue.

A resolver that returns success while the record actually survives — a
provider whose delete is asynchronous, a bug that no-ops, a soft-delete that
leaves data readable — produces a green saga trail over data that is still
present. The trail says erased; the system disagrees. For a data-protection
library that asymmetry is exactly the failure mode the audit trail exists to
catch.

Forces:

- **The base `Resolver` protocol is public API under the strictest stability
  promise (additive-only).** Not every external system supports a cheap
  "is this subject absent?" re-query, and forcing one onto every resolver
  would be a breaking change. So verification must be an *optional*
  capability, exactly like `RectifyingResolver`, `AttestingResolver`, and
  `RetentionOnlyResolver`.
- **A negative verification is information, not a reversal.** If the resolver
  re-queries and the record is still present, that is a fact worth recording
  loudly — but the local erase already happened and the outbox entry already
  succeeded under the resolver's own report. Silently reverting the erase, or
  re-failing a `SUCCEEDED` entry, would fight the idempotency contract and the
  ADR 0010 terminal-state machine. The honest mechanism is to *audit* the
  discrepancy, not to act on it automatically.
- **Verification is a read-back, never a second delete.** It must never mutate
  the external system; it only re-queries and reports.
- **The audit trail keys events by `event_type` strings** — adding members is
  a MINOR format change (ADR; `AuditEventType` is a `StrEnum` stored as its
  value), needs no DB migration.

## Decision

**Add an optional `VerifyingResolver` capability sub-protocol** and have the
saga runner call it after a successful external erasure, only when the
resolver implements it — silently skipped otherwise, exactly as with the three
existing capability sub-protocols.

- **`VerifyingResolver(Resolver, Protocol)`** (`resolvers/verifying.py`,
  `@runtime_checkable`) adds one method:
  `async def verify_absent(self, ref: SubjectRef) -> ResolverVerification`. The
  base `Resolver` protocol is untouched.
- **`ResolverVerification`** (`resolvers/verification.py`) is a frozen
  `extra="forbid"` pydantic model mirroring `ResolverErasure`:
  `resolver: str`, `confirmed_absent: bool`, `detail: str | None`, and a
  timezone-aware `checked_at` timestamp. `confirmed_absent=True` means the
  re-query found the subject gone; `False` means it is still present despite
  the erase reporting success.
- **The saga runner verifies after a successful erase only.** In
  `_succeed`, after a `ResolverErasure` has been audited and the entry marked
  succeeded, if `isinstance(resolver, VerifyingResolver)` the runner awaits
  `verify_absent(ref)` and appends `ERASURE_EXTERNAL_VERIFIED`
  (`confirmed_absent=True`) or `ERASURE_EXTERNAL_VERIFICATION_FAILED`
  (`confirmed_absent=False`). A resolver that does not implement the
  capability produces **no** event and **no** error — the silent-skip
  precedent of `RectifyingResolver`/`AttestingResolver`. Verification only
  runs for on-demand `ResolverErasure` outcomes: a `RetentionOnlyResolver`'s
  scheduled/verified-expiry success already carries its own `verified_expiry`
  fact (ADR 0022) and is not re-verified here.
- **A negative verification does not, by itself, revert the erase.** The entry
  stays `SUCCEEDED`; `ERASURE_EXTERNAL_VERIFICATION_FAILED` is the durable,
  loud record of the discrepancy for an operator to act on. This is a
  deliberate separation: effaced records that the external system disagreed
  with the resolver's report; it does not silently re-drive deletion or
  re-open a terminal entry. Re-issuing erasure for such a subject is an
  operator decision (re-enqueue), not an automatic one.

The verification append follows the same audit-before-status discipline as the
rest of the runner only in spirit: the step success and completion events are
already written and the entry already marked before verification runs, so a
verification event is strictly *additional* trail after a settled success. A
verification call that itself raises is swallowed exactly like the abandonment
hook is isolated (it must never corrupt the settled erase or its trail); the
discrepancy simply goes unrecorded that run, and a later claim/verification can
re-observe it — verification is an *additional* assurance, never a gate the
erase waits on.

## Consequences

- **MINOR under widened SemVer.** The base `Resolver` protocol is untouched;
  `VerifyingResolver` is a new optional capability and `ResolverVerification`
  a new model, both additive. No erasure or export *result* changes for any
  input — a non-verifying resolver behaves byte-identically to today. Two new
  `AuditEventType` members are an additive format change (MINOR), and because
  the audit table stores `event_type` as a string, **no DB migration** is
  required.
- **A green saga trail over surviving external data is now catchable.** When a
  resolver implements `verify_absent`, the trail carries an independent
  read-back verdict — `ERASURE_EXTERNAL_VERIFIED` confirms absence,
  `ERASURE_EXTERNAL_VERIFICATION_FAILED` records a discrepancy the resolver's
  own report missed.
- **The conformance suite grows a skip-gated section** (`verify_absent` after
  erase confirms absence; a still-present subject reports
  `confirmed_absent=False`), inherited by every resolver package; non-verifying
  resolvers skip it, never fail it.
- This is a **mechanism** for an optional independent read-back and an
  auditable record of its verdict. It is never a determination that an
  external system holds no personal data: `verify_absent` re-queries the same
  surface the resolver reaches, and a confirmed absence is execution-fidelity
  evidence, not a compliance conclusion.
