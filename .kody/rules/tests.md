<!-- @kody-sync -->
# Tests (packages/*/tests/)

- Every behaviour change needs a test; erasure/export changes need cross-subject-bleed and retention-preservation proofs (property tests where feasible).
- No live network calls — external systems are faked behind the `Resolver` protocol.
- Integration tests carry `@pytest.mark.integration` and read `EFFACED_TEST_DATABASE_URL`; everything else runs without services.
- Test public behaviour through public API; flag tests poking private attributes.
