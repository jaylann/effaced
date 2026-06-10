# 0007. Erasure plan semantics: row deletion vs. in-place anonymization

- **Status:** accepted
- **Date:** 2026-06-10

## Context

`ErasurePlanner.plan()` must turn per-column declarations (`DELETE` / `ANONYMIZE` / `RETAIN`) into an inspectable per-table programme. The hard question is when erasing a subject may remove a whole row versus when it must keep the row and scrub columns in place. Deleting too much destroys business data the manifest never declared (and can break referential integrity for retained records); deleting too little leaves personal data behind. These semantics decide *what gets deleted*, so under widened SemVer (ADR 0003) any later change to them is MAJOR — they have to be settled, written down, and golden-tested before the first executor ships.

## Decision

For each table reachable from the subject (its `TableAccessPlan` in the `SubjectGraph`), with **A** = its annotated columns:

1. **Row deletion** happens iff *every* column in A is `DELETE` (vacuously true when A is empty, e.g. link-only tables) **and** the table is *fully PII-owned*.
   - *Fully PII-owned* (computed by the adapter at resolution time, carried as `TableAccessPlan.fully_pii_owned`): every physical column is PII-annotated, a primary-key member, or a foreign-key member. Keys are structural plumbing, not retained content; anything else (an unannotated payload column) means row deletion would erase more than the manifest declares. The field defaults to `False` — hand-built graphs must opt in explicitly.
2. **Otherwise the row survives** and the plan emits column-level steps:
   - one `ANONYMIZE` step naming every non-`RETAIN` column in A. `DELETE` columns on a surviving row are **anonymized with a type-valid surrogate, never set to `NULL`** — `NOT NULL` and unique constraints must keep holding, and an irreversible surrogate is content erasure;
   - one `RETAIN` step naming the `RETAIN` columns — an explicit, auditable record of the retention decision (skip semantics: the executor touches nothing, the audit trail says so).
   - A table with no annotated columns that is not fully PII-owned produces **no local step**: nothing is declared erasable and the row may not be deleted. The completeness check, not the planner, is the place that flags such tables.
3. **Conflicts fail loudly before anything runs.** If a surviving table's hop chain passes through a table planned for row deletion, the plan is unsatisfiable (deletion would orphan the surviving rows):
   - `RetentionViolationError` when the survivor holds a `RETAIN` column — a retention duty blocks the deletion;
   - `ManifestError` when the survivor merely has nothing erasable declared — the manifest is incomplete, no legal duty is at stake.
4. Local steps follow `SubjectGraph.deletion_order` (children before parents, subject last); within a table, `ANONYMIZE` precedes `RETAIN`. External steps — one whole-subject `DELETE` per registered resolver, in registration order — always trail the local steps; the plan records the caller's `SubjectRef`s for the executor.
5. Surrogate values come from the extensible `SurrogateRegistry` (SQLAlchemy adapter), resolved by column-type MRO with loud `AnonymizationError` on unknown types. The registry is consumed only at execution time: plans carry no values, which keeps `plan()` a pure, deterministic function with no session and no I/O.

## Consequences

- Plans are fully inspectable contracts: golden tests pin the exact step tuple for a schema, and property tests pin the invariants (`RETAIN` columns never appear in a `DELETE`/`ANONYMIZE` step; row deletion requires full ownership + all-`DELETE`).
- Conflict detection walks hop chains only. A surviving table that FK-references a row-deleted table *outside* its subject path is not caught at plan time; the local transaction then fails loudly with a database integrity error — never silent data loss. Widening detection to all FK edges would need adapter FK data in core and can be added compatibly later.
- Self-referential FKs (e.g. comment threads) and cross-subject parent rows are an executor concern; the plan only fixes table-level order.
- Tables wholly owned by the subject but containing unannotated payload columns are anonymized in place rather than deleted; teams that want row deletion there must annotate the remaining columns. This is the conservative direction: the planner never deletes more than the manifest declares.
- Any change to these rules alters erasure results for existing manifests ⇒ MAJOR release, `breaking` label, PR-body declaration (ADR 0003).
