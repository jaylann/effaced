<!-- @kody-sync -->
# Erasure semantics (packages/effaced/src/effaced/erasure/, saga/)

- Any change to WHAT gets deleted or exported is breaking, even with identical signatures — require the `breaking` label and an explicit PR-body declaration. Flag undeclared behaviour changes as blockers.
- Fields with `ErasureStrategy.RETAIN` must never be deleted by any code path; the planner raises `RetentionViolationError` instead of guessing. A `RETAIN` without `RetentionPolicy` must be impossible (validator).
- Local erasure steps run in ONE atomic transaction; outbox entries enqueue in the SAME transaction. Any code separating these reintroduces the half-erased-state bug.
- Erasure plans must be inspectable before execution (no side effects in `plan()`).
