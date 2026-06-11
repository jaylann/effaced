# 0013. Rectification: category-keyed corrections, saga fan-out, value-free audit

- **Status:** accepted
- **Date:** 2026-06-11

## Context

Art. 16 gives the data subject the right to have inaccurate personal data rectified without undue delay; Art. 19 obliges the controller to communicate that rectification to each recipient the data was disclosed to, unless impossible or disproportionate. effaced already knows where a subject's PII lives — the data map locally, refs→resolvers externally (ADR 0008) — so rectification is the natural third mechanism after export (Art. 15) and erasure (Art. 17): apply a correction across the mapped schema and fan it out to resolvers, which is the mechanical half of the Art. 19 duty (informing *other* recipients remains the controller's process).

Issue #46's open questions are exactly the ones that must be pinned before code: how corrections are expressed, how the additive-only `Resolver` protocol grows, and how the trail records a correction without putting old or new values — both PII — into it. Rectification changes stored data, so under widened SemVer (ADR 0003) everything decided here is MAJOR to change later.

## Decision

### Corrections are keyed by `PiiCategory`, not by column

A correction is `Correction(category, value)` (frozen pydantic); a rectification call carries a tuple of them. Category is the right key for three reasons: it is the only vocabulary shared with resolvers (external systems have no notion of our columns); Art. 16 is about *accuracy*, and a category-wide write is what keeps denormalized copies of the same fact consistent — correcting one `email` column and missing its copy is a fresh inaccuracy; and column addressing would leak schema into the request layer. Per-column corrections were considered and rejected on those grounds.

Locally, every annotated column of the category reachable from the subject (via the `TableAccessPlan` hop chains) is updated to the corrected value. A category matching no local column is not an error — it may match externally, and "nothing local holds that category" is a complete answer.

### Erasure strategy does not gate rectification

`RETAIN` and `ANONYMIZE` columns of the category are rectified too. Strategy governs what happens on *erasure*; an inaccurate record retained under a legal duty is the worst of both worlds, and Art. 16 does not defer to Art. 17 annotations. This is pinned explicitly because it is the kind of asymmetry someone will otherwise "fix" later.

### Resolver protocol: additive optional `rectify_subject`

`Resolver` gains `async def rectify_subject(ref, corrections) -> ResolverRectification` as an **optional** method — the protocol stays additive-only. A registered resolver without the method is skipped and recorded (`skipped_resolvers`), mirroring ADR 0009's treatment of resolvers with no matching ref: capability absence is an honest answer, never an error. `ResolverRectification(resolver, already_consistent=False, detail=None)` mirrors `ResolverErasure`; the idempotency contract is convergence — re-applying a correction the system already reflects returns success with `already_consistent=True`, the rectification analogue of `already_absent`.

### External fan-out reuses the outbox

The half-rectified state is the same bug as the half-erased state, so rectification uses the same cure: external corrections enqueue as outbox entries in the caller's transaction, atomically with the local writes. `OutboxEntry` gains an `operation` discriminator (`"erase"` default, `"rectify"`) and a payload column carrying the corrections. The payload is real PII (corrected values) and must survive retries, so it lives in the row — and is **cleared on terminal transition** (`SUCCEEDED` and `ABANDONED` alike); the audit trail never sees it. ADR 0010's claim, lease, retry, backoff, and abandonment semantics apply to rectify entries verbatim.

Completion becomes per-(subject, operation): `ERASURE_COMPLETED` considers only erase entries, `RECTIFICATION_COMPLETED` only rectify entries. This amends ADR 0010's all-entries predicate, but vacuously for every existing deployment — no rectify entry exists until this ships — so it is not a MAJOR behaviour change.

### Audit: the ADR 0009 sequence, with no values, ever

New additive `AuditEventType` members, mirroring the erasure trail:

1. Validation failures raise before any event (same rule as the exporter and eraser).
2. `RECTIFICATION_REQUESTED` — `{categories, local_steps, external_steps}` (category names comma-joined).
3. One `RECTIFICATION_STEP_SUCCEEDED` per local step — `{target, category, rows}`; on first failure `RECTIFICATION_STEP_FAILED` — `{target, error}` (exception class name only), then re-raise; the caller must not commit.
4. `RECTIFICATION_LOCAL_COMPLETED` — totals plus `skipped_resolvers`.
5. `RECTIFICATION_COMPLETED` — the saga runner's, when the subject's last rectify entry succeeds.

Payloads carry table/column/category names and counts only. **Old and new values never appear in any event** — the no-PII-in-payload rule has no rectification exception.

## Consequences

- This lands as MINOR: an additive protocol method, additive enum members, and nullable/defaulted `effaced_outbox` columns (existing deployments need the additive ALTER, documented in the saga runbook when implemented). Everything above is MAJOR-protected once shipped.
- The corrections payload makes the outbox a temporary PII store. Clearing it at terminal status is part of the mechanism's contract and must be property-tested (no terminal row retains a payload), and the runbook's outbox-inspection guidance must say plainly that in-flight rows contain corrected values.
- A category-keyed write is deliberately blunt: it cannot express "fix this one row but not that one". Schemas where one category legitimately holds per-row-divergent values need per-row mechanisms effaced does not ship; the docs say so rather than pretending.
- Resolvers that implement `rectify_subject` inherit the conformance suite's new idempotency obligation: rectify twice, expect `already_consistent=True` the second time (ADR 0011's suite grows a rectification section when this ships).
- ADR 0015's requeue is operation-agnostic by design; its audit event name follows the entry's operation (`RECTIFICATION_REQUEUED` arrives with this mechanism's implementation).
