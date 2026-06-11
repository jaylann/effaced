# 0008. Ref→resolver matching: a ref's kind names its resolver

- **Status:** accepted
- **Date:** 2026-06-10

## Context

Engines that fan out to external systems (`Exporter.export_subject` today, the erasure executor and saga runner next) receive a flat `refs: tuple[SubjectRef, ...]` and a `ResolverRegistry` — but until now nothing defined *which ref goes to which resolver*. `ErasurePlan.refs` even says "the executor matches them to resolvers at execution time" without saying how. The `Resolver` protocol is public API under the strictest stability promise (additive evolution only), so the matching rule must not require a protocol change. Options considered:

- **`ref.kind == resolver.name`** — one deterministic equality, no new API surface.
- **Cartesian** (every resolver × every ref) — forces every resolver to tolerate foreign kinds; a Stripe resolver handed an `"email"` ref would raise and pollute `incomplete_sources` with non-failures.
- **Declared mapping** (a kinds property on resolvers, or a dict passed to engines) — flexible, but new public surface for a need no resolver has yet; can be added additively later if one ever does.

## Decision

**A ref is routed to the resolver whose `name` equals the ref's `kind`.** Concretely, for any engine fanning out refs:

- A resolver is invoked once per matching ref (several matching refs ⇒ several calls); results merge in registration-then-ref order.
- A ref whose kind matches **no** registered resolver fails loudly (`ResolverError`) before any work or audit event — a typo'd kind must never silently drop an external source from an Art. 15/17 answer.
- A registered resolver with **no** matching ref is *skipped*, and that is a complete answer ("the subject has no identity in that system"), not a failure: it is recorded in the operation's completion audit payload (`skipped_resolvers`), never in `incomplete_sources`.
- A matched resolver call that *fails* is the incomplete case: its name lands in `incomplete_sources` (export) or the saga's failure bookkeeping (erasure).

`StripeResolver` accordingly documents refs as `kind="stripe"` (its `name`), not `"stripe_customer"`.

## Consequences

- The registry remains the single auditable declaration of external systems; ref kinds in application code read as resolver names, greppable in audits and outbox entries.
- Resolver `name` was already format-stable (recorded in audit events and outbox rows); this decision makes the same string the routing key, so renaming a resolver remains MAJOR.
- The future erasure executor and saga runner must reuse this rule — defining a second matching scheme would be a behaviour change to what gets erased/exported (MAJOR under ADR 0003).
- One external system needing several identifier namespaces can still receive them: several refs of its kind (the resolver disambiguates via `value`/`extra`), or an additive declared-kinds extension later.
