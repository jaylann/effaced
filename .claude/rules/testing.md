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
- No live network calls. External systems are faked behind the `Resolver` protocol.
- Every resolver package subclasses `effaced.testing.ResolverConformanceSuite` in its tests (protocol shape, export shape, erase idempotency, error taxonomy — ADR 0011). `effaced.testing.InMemoryResolver` is the reference fake; the stripe package fakes at the HTTP boundary instead (`stripe.HTTPClient` subclass) so the SDK's real status→exception mapping is exercised.
- SQLite silently drops `FOR UPDATE`/`SKIP LOCKED` from compiled SQL — locking/concurrency claims are only provable in the Postgres integration suite; SQLite unit tests cover everything else.
- Extend the shared annotated schema in `packages/effaced/tests/conftest.py` instead of creating parallel fixture schemas.
- Floats via `pytest.approx`; time frozen where timestamps matter.
- Test files stay small and named for what they prove. Basenames must be unique across every `packages/*/tests/` dir — they share one pytest import namespace (no `__init__.py`), so same-named modules collide at collection.

## Mutation testing (the weekly truth serum)

- `deep-checks.yml` runs mutmut weekly over `audit/consent/erasure/manifest/saga`. A **surviving mutant is a missing test**: mutmut changed the code and no test failed, so that behaviour is unpinned. Treat the survivor list (job summary / `mutmut-results` artifact) as a to-do list, not noise.
- Working survivors locally: `cd packages/effaced && uv run mutmut run`, then `uv run mutmut results` to list and `uv run mutmut show <mutant-id>` to see the exact diff a survivor represents. Write the test that fails under that diff (verify red against the mutant reasoning, green on real code), don't just chase the coverage line — covered-but-unasserted code is exactly what survivors expose.
- A few survivors are *equivalent mutants* (the mutation provably cannot change observable behaviour, e.g. a mutated value that's immediately overwritten). Don't write vacuous tests for those — note them in the PR and move on.
- 🫥 "no tests" mutants mean no test even executes that code — those need a test for the code path itself, not a sharper assertion.
- The mutation job is report-only today. Once the survivor count is driven to the equivalent-mutant floor, flip it to a hard gate (fail on survivors above that floor) in `deep-checks.yml`.
