# 0020. FastAPI integration layer: a router over an app-supplied subject

- **Status:** accepted
- **Date:** 2026-06-12

## Context

The smallest honest FastAPI integration (`examples/fastapi-quickstart`)
was ~150 lines, and almost all of it was mechanical: collect the data
map, resolve the subject graph, mount the owned tables, construct the
audit sink, the outbox, and one engine per article, then hand-write the
same three routes every application needs. The wiring is identical in
every app; only two things are genuinely application-specific — which
models are annotated, and who the authenticated subject is.

Framework ergonomics is the roadmap ask: integration in five lines, not
fifty. Constraints that shape the answer:

- ADR 0006 fixes the engine API as sync `def`; `async def` is permitted
  only on `Resolver` methods and `SagaRunner.run_once`.
- FastAPI must never leak into core — `effaced` has users with no web
  framework at all.
- Widened SemVer (ADR 0003): anything that changes what gets deleted or
  exported is MAJOR; an HTTP layer must not invent response shapes that
  could drift from engine behaviour independently.
- The saga-runner-wiring runbook already names the one wrong way to
  drain the outbox in FastAPI: a task on the serving event loop.

## Decision

### The wiring facade lives in core adapters; the new package is FastAPI-only

`EffacedStack.from_base(base, session_factory, *, resolvers/registry,
audit_sink)` lands in `effaced/adapters/sqlalchemy/` — it touches
SQLAlchemy, so adapters is its sanctioned home — and performs exactly
the quickstart sequence, returning every wired engine as a named handle.
It adds no behaviour: each handle is the component you could have
constructed by hand, and construction executes no SQL (the owned tables
still ride the caller's migrations, ADR 0018). A future Django/Flask
layer reuses the same facade instead of forking twelve constructor
calls. Additive, so an `effaced` MINOR.

`packages/effaced-fastapi` then holds only FastAPI concerns:
`EffacedFastAPI` (constructor builds a stack or adopts a prewired one),
`router(subject=...)`, `lifespan()`, the request models, and
`SagaWorker`.

### Routes are plain `def`; ADR 0006 stands unamended

Every mounted route is a sync `def` — FastAPI runs it on its threadpool,
so the engines never block the event loop, and the app's (possibly
async) auth dependency still resolves on the loop. A `def` route
depending on an `async def` dependency is supported FastAPI behaviour
and pinned by a test. The only `async def` in the package is
framework-boundary glue — the lifespan contextmanager and the worker's
private drain loop — never engine API; this ADR is the sanction that
rule asks for.

### Subject identity is the application's, always

The router authenticates nothing and discovers nothing. An app-supplied
dependency returns `Subject(subject_id, refs)`: the id the annotated
models key on, and the external refs (kind → resolver, ADR 0008) the app
stored at signup. A request with no Stripe ref simply doesn't reach
Stripe. This is the same explicit-registration stance as the resolver
registry: who the subject is, and where else they live, are auditable
application declarations — never library guesses.

### Responses are the engines' result models

`GET /export` returns `ExportBundle`; `DELETE` returns `ErasureResult`;
the consent and restriction routes return the `ConsentRecord` /
`RestrictionRecord` they appended. No endpoint-specific shapes exist, so
an endpoint's payload changes exactly when the underlying engine's
behaviour does — widened SemVer applies once, at the engine, instead of
twice. Route paths and defaults are public API of `effaced-fastapi`;
changing them is MAJOR for this package.

### One transaction per request, overridable by identity

The default session dependency wraps each request in
`session_factory.begin()` — the local erasure phase and its outbox
enqueue commit or roll back together, preserving the atomic-local-phase
guarantee (ADR 0009). It is built once in `__init__` so
`app.dependency_overrides[gdpr.session_dependency]` works by object
identity; `router(session=...)` overrides per router.

### The saga drain is a daemon thread, packaged

`SagaWorker` packages the runbook's blessed pattern: a daemon thread
running its own `asyncio.run` loop over `run_once`, polling gently when
the queue is empty, logging and retrying on a failed batch (a silently
dead drain thread would stall data-subject requests). `gdpr.lifespan()`
wraps it for apps without their own lifespan. It is explicitly NOT an
`asyncio.create_task` on the serving loop — `run_once` makes blocking
database calls between awaits (ADR 0006).

### No rectification endpoint

Art. 16 stays off the router. A self-serve correction endpoint would let
a subject rewrite any annotated column of their categories; which
corrections an application permits is an authorization decision the
library must not make. Apps call `stack.rectifier.rectify_subject` from
their own route, behind their own rules. Revisit only with an explicit
allowlist design and its own ADR.

### Restriction is opt-in; consent status is on by default

`POST /consent`, `GET /consent/{purpose}`, `GET /export`, and `DELETE`
at the include prefix are always mounted. `POST /restriction` and
`GET /restriction` sit behind `router(restriction=True)` — Art. 18
flag-keeping is meaningless until the app decides what the flag gates
(ADR 0014), so it should be a conscious switch.

## Consequences

- The quickstart's integration drops to a stack, a subject dependency,
  and one `include_router`; the example now dogfoods the package and is
  the living proof of the five-line claim.
- `EffacedStack` is load-bearing public core API: its field names and
  `from_base` signature carry the same additive-evolution promise as the
  engines it wires.
- Route paths and response models are public API of `effaced-fastapi`
  under widened SemVer; the response shapes themselves remain governed
  by core (one source of truth).
- A second framework layer (Django/Flask) is now a thin package over
  `EffacedStack` — the facade was the actual ergonomics work.
- FastAPI route closures cannot use `Annotated[X, Depends(local)]`
  under `from __future__ import annotations` (string annotations resolve
  module globals only); the package uses `= Depends(...)` defaults, with
  `fastapi.Depends` exempted from B008. A constraint to carry into any
  future framework layer that builds routes in closures.
