# 0025. Composite subject identity keys

- **Status:** accepted
- **Date:** 2026-06-18

## Context

A data subject was identified by a single column. `SubjectGraph` carried one
`subject_id_column: str`, the public `subject_id` was one scalar coerced to
that column's type, and `subject_scope` anchored every hop chain on that one
column. Schemas whose subject identity spans several columns — the common
multi-tenant `(tenant_id, user_id)` shape, or any natural composite key —
could not be expressed at all (#129).

This surfaced while widening the generated-schema strategy (#122): composite
*foreign-key hops* between tables are exercised, but composite *subject keys*
were explicitly left out of scope because the single-column field could not
represent them. The hop-chain machinery already speaks row-value tuples (the
`grouped()` helper builds `tuple_(...)` for composite FKs); only the subject
anchor itself was scalar-bound.

Forces:

- **Identity matching is data-protection-relevant.** A partial-key match —
  scoping on `tenant_id` alone when identity is `(tenant_id, user_id)` —
  would erase or export *other* subjects' rows. Under widened SemVer that is
  the worst possible failure, so the matching predicate must bind the *whole*
  ordered key or nothing.
- **Single-column schemas must not change behaviour.** The overwhelmingly
  common case is one column; its SQL, its stored references, and its audit
  trail must stay byte-identical, or this is a silent erasure-behaviour
  change for every existing user.
- **The audit trail and the outbox key subjects by an opaque scalar.**
  `AuditEvent.subject_ref` and the outbox completion-grouping key are
  strings; a composite identity has to collapse to one deterministic string
  without ever colliding two distinct subjects into one group.
- **Arity is per-application, fixed by the schema.** A given manifest's
  subject key has a known, constant number of columns; effaced never matches
  a partial key, so the public identifier and the declared columns must agree
  on arity at the call boundary.

## Decision

**Subject identity is an ordered tuple of column values.** A new frozen
pydantic model `CompositeSubjectId(values: tuple[str, ...])` (`min_length=1`,
empty-string elements rejected) carries it natively at the API layer, and the
public alias `SubjectIdentifier = str | CompositeSubjectId` is what every
engine entry point now accepts. The tuple is **positional**, aligned
left-to-right to the declared subject-id columns.

**A plain `str` is the one-element canonical form.** `canonical_subject_id`
returns a bare `str` *unchanged*; a single-column subject is therefore
byte-identical to today in storage, audit refs, and SQL. There is no
migration of caller code that already passes a string.

**Canonical serialization is deterministic and collision-free.**
`canonical_subject_id(CompositeSubjectId(("a", "b")))` joins the escaped
element values on a reserved separator (`\x1f`, the ASCII unit separator),
escaping the separator and the escape character in each element first. This
is what makes `("a", "b:c")` and `("a:b", "c")` — and any other arity-or-
boundary ambiguity — serialize to provably distinct strings. Saga
completion-grouping and cross-subject isolation both depend on that
distinctness, so the escaping is load-bearing, not cosmetic.
`parse_canonical` is its exact inverse.

**Storage is a scalar canonical string in the existing columns — no DDL
change.** The canonical string lands in the existing `subject_id`/
`subject_ref` `String(255)` columns on the owned tables. effaced matches the
*whole* identity, never a partial key, so the database never needs to see the
key decomposed; it stores one opaque scalar exactly as before. The owned-
table DDL is untouched, so no Alembic migration is required (ADR 0021).

**The manifest's `SubjectLink` is the serialized, migrated half;
`SubjectGraph` stays runtime-only.** `SubjectLink.subject_id_column: str` is
generalized to `subject_id_columns: tuple[str, ...]` and is serialized into
the manifest, so the format change bumps `MANIFEST_SCHEMA_VERSION` 2 → 3 with
a forward migration that lifts each old single `subject_id_column` into a one-
element `subject_id_columns`. Old manifests migrate 1 → 2 → 3 and are never
rejected (ADR 0003). `SubjectGraph.subject_id_columns` is the resolved
runtime mirror; the graph is never serialized, so it needs no migration.

**One composite predicate, shared.** `scoping.subject_scope` builds the
subject anchor over `graph.subject_id_columns` using the existing `grouped()`
row-value helper: one column produces `col == value` exactly as before
(byte-identical SQL), several produce a row-value tuple equality. The
exporter and retention sweeper, which each forked their own subject
EXISTS/attribution SQL, converge onto `scoping.subject_scope` so there is a
single composite-matching implementation — the same discipline the executors
and verifier already follow (CLAUDE.md: never fork the scoping predicate).

`scoping` lives under `adapters/sqlalchemy/`; the exporter and sweeper are in
core. Importing the shared predicate up from the adapter is the accepted
trade for a single implementation: the semgrep gate forbids only a *direct*
`import sqlalchemy` in core, `import effaced` already hard-depends on the
SQLAlchemy adapter through the root re-exports, and a forked second predicate
on the data-protection-critical matching path is the worse risk. The
alternative — relocating the predicate into core — is blocked by the
row-value tuple constructor (`tuple_`) a composite key needs, which core may
not import.

## Consequences

- **Composite-subject schemas now match on the full ordered key.** Two
  subjects that share one key element (same `tenant_id`, different
  `user_id`) are isolated: an operation on one never touches the other. This
  is a new, tested guarantee — the shared-key-element bleed proof in
  `PROOFS.md`.
- **Single-column schemas are byte-identical.** Stored references, audit
  refs, and emitted SQL are unchanged for every existing user; the canonical
  form of a one-element key *is* the bare string.
- This is **MAJOR** under widened SemVer: the public `subject_id` type
  widens to `SubjectIdentifier`, several result/response models echo it back,
  and the manifest format changes. It carries the `breaking` label and a
  `BREAKING CHANGE:` footer even though no single-column behaviour shifts —
  the widened-SemVer rule is about the *surface*, and a reviewer must see the
  declaration.
- The published `subject_id: str` becomes `subject_id: SubjectIdentifier`;
  `str` remains a valid argument everywhere, so existing call sites compile
  and behave unchanged.
- A composite identifier whose arity disagrees with the declared
  `subject_id_columns` is a loud `SubjectResolutionError` at the call
  boundary — effaced never matches a partial key, so an arity mismatch can
  never be silently coerced into one.
- This is a mechanism for matching the identity an application declares, never
  a determination that any particular key is the legally correct subject
  identifier — that remains the controller's decision.
