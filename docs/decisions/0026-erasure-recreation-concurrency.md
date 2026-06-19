# 0026. Erasure re-creation and the mid-erasure race

- **Status:** accepted
- **Date:** 2026-06-19

## Context

`ErasurePlanner.erase_subject` runs the local phase atomically in the
caller's session — every table's DELETE/ANONYMIZE/RETAIN and the outbox
enqueue commit or roll back as one unit (ADR 0009). Atomicity is necessary
but not sufficient under concurrency: the method takes **no subject-level
lock**. The only `FOR UPDATE` in the engine is the saga runner's
entry-level lock in `Outbox.mark_succeeded`/`requeue`, which serializes
*completion* on a subject's outbox rows, not the local erasure itself.

Two races follow, both data-protection-relevant (#A1):

1. **Concurrent erasures of one subject.** Two `erase_subject` calls for the
   same subject — a retried HTTP request, two operators, a replay racing a
   live request (ADR 0023) — interleave. Each reads the subject's rows,
   plans, and deletes; the two transactions can both succeed, double-enqueue
   external work, and emit two overlapping `ERASURE_REQUESTED …
   LOCAL_COMPLETED` audit spans with no record that they collided. Resolver
   idempotency absorbs the duplicate external calls, but nothing serializes
   the local phase or makes the collision visible.

2. **An application INSERT racing the DELETE.** While erasure deletes the
   subject's rows in FK-safe order, an ordinary application write inserts a
   *new* row for the same subject — a fresh comment, order, or session row.
   Read-committed isolation lets that INSERT commit against rows the erasure
   has not reached yet (or against the anchor row before it is anonymized),
   so the subject is left with freshly-created PII after an erasure the trail
   records as complete. This is the classic "the data came back" failure.

There is today no tombstone, marker, or serialization point for "this
subject is being / has been erased". Distinct from these two races is
**post-completion re-creation**: after `erase_subject` has fully committed,
the application re-creates the subject (a re-signup, a webhook replaying an
old event). No lock can prevent that — the erasure is over — and effaced
must not claim to.

Forces:

- **Identity-scoped serialization, not a global lock.** Erasures of
  *different* subjects must stay fully concurrent; only same-subject
  erasures may serialize. The subject's canonical id is the natural lock
  key.
- **Lock in-flight writes, not just other erasures.** Serializing erasures
  against each other does nothing about race 2 — an application INSERT is
  not an erasure. The erasure must hold a lock the application's write path
  contends on. The subject's *anchor rows* (the subject table's rows for
  this identity) are that shared point: a child-row INSERT resolves its FK
  against the anchor, and an app write touching the subject's own row blocks
  on it.
- **The default path must not change.** Widened SemVer: `erase_subject`'s
  behaviour for any single-threaded input must stay byte-identical, or this
  is a silent erasure-behaviour change for every existing caller. The guard
  has to be opt-in wiring, off by default.
- **Wording discipline.** effaced ships a *mechanism* that serializes and
  locks; it does not *determine* that re-creation is prevented. The
  controller owns what happens after completion.
- **No deadlock against the existing saga lock.** A new `FOR UPDATE` path
  must provably not invert lock order against `Outbox.mark_succeeded`'s
  entry-level `FOR UPDATE`.

## Decision

**effaced serializes erasures of one subject and locks the subject's anchor
rows for the duration of the local phase.** Two collaborating pieces:

1. **A `subject_erasures` tombstone table** (effaced-owned, ADR 0021),
   keyed by the subject's canonical `subject_ref` (`String(255)` primary
   key, the same scalar the audit trail and outbox store — ADR 0025). It
   carries `requested_at`, a nullable `erased_at`, and a `status`. The row
   is the **serialization point**: `SubjectLock.acquire` upserts it
   (`status=requested`, a fresh `requested_at`) and takes `SELECT … FOR
   UPDATE` on it, so a second concurrent erasure of the same subject blocks
   on the row lock until the first commits. It is also the **detection
   surface**: when the local phase succeeds, `SubjectLock.mark_erased`
   stamps `erased_at` and moves the row to `status=completed` *in the same
   transaction*, so the completion mark is durable exactly when the erasure
   is (a rollback takes it with the row changes — the tombstone never
   records a completion that did not commit). A re-creating code path can
   query the row to distinguish a finished erasure (`completed`) from one
   still in flight or crashed mid-erasure (`requested`); a fresh erasure of
   a completed subject re-opens it to `requested`, so the surface always
   reflects the *latest* erasure, never a stale one.

2. **`SELECT … FOR UPDATE` on the subject's anchor rows.** After locking the
   tombstone, `acquire` locks the subject table's rows for this identity,
   reusing the one composite-key anchor predicate from
   `adapters/sqlalchemy/scoping.py` (`subject_scope` over
   `graph.subject_table`) — never a forked predicate (CLAUDE.md). Holding
   that lock for the rest of the caller's transaction makes an in-flight
   application write to the subject's own row, or a child INSERT resolving
   its FK against the anchor, **block until the erasure commits**, closing
   race 2 within one database's isolation guarantees.

**Post-completion re-insertion stays the controller's responsibility.** Once
the erasure transaction commits, no lock is held and the subject can be
re-created by the application — a re-signup is a *new* subject relationship,
not a leak. effaced provides the tombstone as the **detection surface** for
that decision (the controller can check it before re-creating, or sweep it),
and it does **not** claim to prevent re-creation. Saying otherwise would be a
determination effaced does not make.

**Opt-in wiring, default behaviour unchanged.** `ErasurePlanner.__init__`
gains an optional keyword-only `lock: SubjectLock | None = None`.
`erase_subject` calls `lock.acquire(session, subject_id)` **before** the
`ERASURE_REQUESTED` audit event and before the first step, *only when a lock
is wired*. With the default `lock=None` the method is byte-identical to
today, so every existing caller is unchanged; a caller who wants the
guarantee constructs its own `ErasurePlanner` with a `SubjectErasureLock`
explicitly. `EffacedStack.from_base`/`from_manifest` deliberately do **not**
wire the lock by default — auto-wiring would change those facades' behaviour
for every existing user and widen the breaking surface, so opting in stays an
explicit, per-planner decision; a future MINOR may add an opt-in flag.

**`SubjectLock` is a core protocol; `SubjectErasureLock` is its SQLAlchemy
implementation.** `erasure/subject_lock.py` holds the SQLAlchemy-free
protocol (`acquire(session, subject_ref) -> None` and
`mark_erased(session, subject_ref) -> None`, the bracket around the local
phase); `adapters/sqlalchemy/subject_erasure_lock.py` builds the tombstone
upsert, the two `FOR UPDATE` statements, and the completion update off the
bound `Table` handles, keeping core import-clean (ADR 0006/python.md).

### The lock-order invariant

`acquire`, and the local phase that follows it, take locks in exactly this
order, every time:

> **tombstone row → anchor rows → local-step deletes/updates → outbox
> enqueue**

- The tombstone row is locked first (one row, by primary key).
- The anchor rows are locked next (`subject_scope` over the subject table).
- The local steps then DELETE/ANONYMIZE child-to-parent in FK-safe order
  (`graph.deletion_order`), reaching the anchor (subject) table last —
  already locked, so no new lock-acquisition order is introduced there.
- The outbox enqueue inserts new rows last; an INSERT takes no pre-existing
  row lock that could invert against the above. `mark_erased` then updates
  the already-locked tombstone row (the one `acquire` holds `FOR UPDATE`),
  so it acquires no new lock either.

**Why this cannot deadlock against the saga runner.** `Outbox.mark_succeeded`
and `Outbox.requeue` take `FOR UPDATE` on `effaced_outbox` rows in a
*separate, runner-side* transaction, after the erasure has committed and its
entries are durable — the runner only ever sees committed outbox rows. The
erasure transaction never holds an `effaced_outbox` row lock while acquiring
the tombstone/anchor locks: its only outbox interaction is the final
*INSERT* (`enqueue`), which locks no existing row. So the two transactions
never hold-and-wait on each other's resources — there is no cycle, hence no
deadlock. Within the erasure path, the fixed tombstone→anchor order means two
concurrent same-subject erasures both queue for the tombstone row first and
serialize there, never each holding one resource the other wants.

`acquire` upserts the tombstone idempotently: a first erasure inserts the
row, a re-erasure of an already-tombstoned subject records a **fresh
`requested_at`** (the erasure was genuinely re-requested), re-opens it to
`requested`, and re-locks the existing row. The upsert is dialect-portable:
Postgres uses `INSERT … ON CONFLICT DO UPDATE`, which both row-locks the
conflicting row (serializing a concurrent same-subject erasure) and refreshes
it in one statement; other dialects — which never run under real concurrency
because they ignore `FOR UPDATE` — do a plain read-then-`UPDATE`/`INSERT` in
the caller's transaction. A `SAVEPOINT`-based fallback was rejected: a
`SAVEPOINT` opened as the *first* statement on a fresh session auto-begins the
transaction and, on release, can leak the tombstone past the caller's
rollback — the opposite of the same-transaction durability `mark_erased`
depends on. SQLite ignores `FOR UPDATE` entirely, so the serialization and
anchor-lock guarantees are provable only on Postgres (the integration suite),
exactly like the saga's claim locks; the SQLite unit tests cover the tombstone
lifecycle (insert → completed, idempotent re-open, rollback-discards) and the
planner wiring.

## Consequences

- **Concurrent same-subject erasures serialize.** The second blocks on the
  tombstone row until the first commits, then proceeds against the
  first's committed state (its local steps now find the rows already gone —
  the idempotent no-op success of ADR 0009). No more overlapping,
  unrecorded collisions.
- **In-flight application writes to the subject block mid-erasure.** Holding
  the anchor-row lock for the caller's transaction closes the
  INSERT-races-DELETE window under the database's isolation, on dialects
  that honour `FOR UPDATE`.
- **A new effaced-owned table, `effaced_subject_erasures`.** Additive by
  construction (ADR 0021): the caller's next `alembic revision
  --autogenerate` picks it up as one `add_table`, a second autogenerate is a
  no-op, and `metadata.create_all` creates it directly. No migration script
  ships.
- **This is MAJOR under widened SemVer.** It does not change what gets
  deleted or exported for any single-threaded input — the default `lock=None`
  path is byte-identical — but it changes the **concurrency contract** of
  `erase_subject` (when a wired lock is present, the method now blocks and
  serializes where it previously raced), and adds a new public protocol and
  table. It carries the `breaking` label and a `BREAKING CHANGE:` footer so a
  reviewer sees the declaration, per the project's surface-level reading of
  widened SemVer (ADR 0025 precedent).
- **effaced does not prevent post-completion re-creation, and says so.** The
  tombstone is a detection surface for a decision the controller owns — a
  mechanism, never a determination.
- **The serialization/anchor-lock guarantees are Postgres-only.** SQLite
  silently drops `FOR UPDATE` (testing.md), so the concurrency proof lives in
  the Postgres integration suite; the SQLite unit tests cover the tombstone
  upsert's idempotency and the planner wiring.
