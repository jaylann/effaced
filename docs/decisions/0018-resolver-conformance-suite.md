# 0018. effaced.testing: a shipped resolver conformance suite

- **Status:** accepted
- **Date:** 2026-06-11

## Context

Issue #17 requires a shared contract test suite — export shape, erasure idempotency, error taxonomy — that every resolver package (effaced-stripe first) runs against its real implementation. The `Resolver` protocol's promises (already-gone erasure is success, `ResolverError` only for non-retryable faults, transient errors propagate for saga retry) are exactly what the saga runner and exporter rely on; each resolver re-proving them ad hoc would drift. Options considered:

- **Ship `effaced.testing` in the core package** — downstream tests subclass one suite; the contract is versioned with the protocol it tests.
- **Copy a suite into each resolver package** — drifts immediately; a contract test that can drift is not a contract test.
- **A separate `effaced-conformance` distribution** — release overhead for one module, and it could lag the protocol version it certifies.

Two frictions with shipping it in core: the suite needs `pytest` (not a runtime dependency), and it must drive async resolver methods from sync test methods, i.e. an `asyncio.run` site outside the single bridge sanctioned by ADR 0006.

## Decision

**Ship `effaced.testing` as a public subpackage of effaced core**, containing `ResolverConformanceSuite` (the contract as inheritable tests) and `InMemoryResolver` (the reference fake the suite itself is verified against, and a stand-in external system for application tests).

- `effaced.testing` is **never imported by `effaced/__init__.py`**: `import effaced` must not require pytest. Importing `effaced.testing` outside a test environment fails on the pytest import by design — the module is test machinery and says so.
- The suite's private `_run` helper is the **second sanctioned `asyncio.run` bridge** (semgrep exclude updated): pytest test methods are sync, so no event loop is running by construction — the failure mode ADR 0006 guards against cannot occur there.
- Resolver packages subclass the suite with a `Test`-prefixed class and implement the factory hooks (`make_resolver`, `make_present_ref`, `make_absent_ref`, plus optional fault hooks); pytest collects the inherited tests. The base class carries no `__init__` and no `Test` prefix, so it is never collected itself.

## Consequences

- `effaced.testing` is public API under the additive-evolution promise: new test methods in the suite are MINOR (downstream packages inherit stricter checks on upgrade — intended), removing or weakening one is MAJOR.
- A resolver that passes the suite is certified against the protocol version it ships with; CI for every resolver package re-runs the contract on every change.
- pytest stays a dev-group dependency; the import boundary (`effaced/__init__.py` never reaches `effaced.testing`) is what keeps that honest.
- New `asyncio.run` sites still need an ADR — the semgrep rule now names exactly two sanctioned locations.
