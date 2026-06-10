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
- Extend the shared annotated schema in `packages/effaced/tests/conftest.py` instead of creating parallel fixture schemas.
- Floats via `pytest.approx`; time frozen where timestamps matter.
- Test files stay small and named for what they prove.
