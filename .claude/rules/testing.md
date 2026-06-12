---
paths: ["**/tests/**"]
---

# Testing standards

- pytest + hypothesis. Markers: `property` (hypothesis), `integration` (needs real Postgres via `EFFACED_TEST_DATABASE_URL`; excluded from default runs, executed by CI's Postgres job).
- TDD: new behaviour starts with a failing test. Verify the red state before making it green.
- Test public behaviour through the public API; don't poke privates.
- The suite is the project's conformance evidence. Erasure/export changes MUST include proofs of:
  - no cross-subject bleed (subject A's operation never touches B's data),
  - retained-category preservation (RETAIN fields survive every plan),
  - idempotent convergence (re-running a saga step == running it once),
  - fault-injection outcomes (resolver failure leaves a known, audited state).
- **`PROOFS.md` (repo root) maps every published guarantee to the tests proving it.** Renaming, moving, or deleting a listed test updates PROOFS.md in the same PR; a new guarantee lands with its row added.
- Cross-cutting properties run on *generated* schemas: `packages/effaced/tests/schema_strategies.py` (`annotated_schemas()`) draws table trees, links, and strategies through the real `collect_data_map`/`resolve_subject_graph` path. Budget schema-per-example tests with its `scaled_examples(n)` instead of hard-coding `max_examples` (hypothesis profiles activate in `pytest_configure`, before module import, so reading `settings.default.max_examples` at module level scales per profile).
- Generator gotchas: imperative mapping (`registry().map_imperatively(...)` + `registry.configure()`) satisfies `resolve_subject_graph` without a declarative Base — but SQLAlchemy registries hold mapped classes **weakly**, so dynamically created classes need a strong reference for the mapping's lifetime (`GeneratedSchema.classes`) or `registry.mappers` empties intermittently under GC.
- No live network calls. External systems are faked behind the `Resolver` protocol.
- Every resolver package subclasses `effaced.testing.ResolverConformanceSuite` in its tests (protocol shape, export shape, erase idempotency, error taxonomy — ADR 0011). `effaced.testing.InMemoryResolver` is the reference fake; the stripe package fakes at the HTTP boundary instead (`stripe.HTTPClient` subclass) so the SDK's real status→exception mapping is exercised; the s3 package fakes at the client-protocol boundary (`FakeS3Client` implements `S3ObjectClient`) and raises **real** `botocore` exceptions for the same reason.
- SQLite silently drops `FOR UPDATE`/`SKIP LOCKED` from compiled SQL — locking/concurrency claims are only provable in the Postgres integration suite; SQLite unit tests cover everything else.
- Extend the shared annotated schema in `packages/effaced/tests/conftest.py` instead of creating parallel fixture schemas.
- Adding ANY plain (unannotated) column to a shared-conftest table ripples into exact-shape tests — audit all four before extending the schema: the completeness-linter complement (`test_completeness_linter.py`), the table's `fully_pii_owned` classification (`test_resolution.py` — it can silently flip a table from row-delete to anonymize if every other column was PK/FK/annotated), and the full-row dict assertions (`test_erase_subject.py`, `test_erasure_executor.py`, `test_end_to_end_fault_injection.py`).
- `test_bind_tables.py::test_no_server_defaults_*` pins "python-side defaults only" with exactly one carve-out: `effaced_outbox.operation` (server default so the additive ALTER backfills populated outboxes — ADR 0013). A new column needing a server default extends that test's exception list consciously, never silently.
- Floats via `pytest.approx`; time frozen where timestamps matter.
- The dev (default) hypothesis profile keeps the 200ms per-example deadline that the `ci`/`deep` profiles disable — property tests doing several engine operations per example need `@settings(deadline=None)` or they flake under coverage tracing.
- Test files stay small and named for what they prove. Basenames must be unique across every `packages/*/tests/` dir — they share one pytest import namespace (no `__init__.py`), so same-named modules collide at collection.

## Mutation testing (the weekly truth serum)

- `deep-checks.yml` runs mutmut weekly over `audit/consent/erasure/manifest/saga`. A **surviving mutant is a missing test**: mutmut changed the code and no test failed, so that behaviour is unpinned. Treat the survivor list (job summary / `mutmut-results` artifact) as a to-do list, not noise.
- Working survivors locally: `cd packages/effaced && uv run mutmut run`, then `uv run mutmut results` to list and `uv run mutmut show <mutant-id>` to see the exact diff a survivor represents. Write the test that fails under that diff (verify red against the mutant reasoning, green on real code), don't just chase the coverage line — covered-but-unasserted code is exactly what survivors expose.
- A few survivors are *equivalent mutants* (the mutation provably cannot change observable behaviour, e.g. a mutated value that's immediately overwritten). Don't write vacuous tests for those — note them in the PR and move on.
- 🫥 "no tests" mutants mean no test even executes that code — those need a test for the code path itself, not a sharper assertion.
- The mutation job is report-only today. Once the survivor count is driven to the equivalent-mutant floor, flip it to a hard gate (fail on survivors above that floor) in `deep-checks.yml`.
