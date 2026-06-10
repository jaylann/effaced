# 0006. Session strategy: sync-first engine API

- **Status:** accepted
- **Date:** 2026-06-10

## Context

The pre-0.1 skeleton mixed `async def` engine methods with sync `sqlalchemy.orm.Session` parameters. Every engine signature is public API under widened SemVer (ADR 0003), so the sync/async shape must be settled before 0.1 — changing it later is MAJOR. Options considered:

- **Sync-first** — sync core taking the caller's `Session`; `async def` only where I/O is inherently async (resolver HTTP, saga runner).
- **Async-native** — `AsyncSession` everywhere. Excludes sync apps (Flask, Django, scripts, cron): they cannot produce an `AsyncSession` without configuring a second engine plus an async driver just for effaced. GDPR-needing apps skew heavily sync.
- **Dual surface** — both, via dual classes or `unasync` codegen. Doubles API, tests, and docs pre-0.1 for operations that are rare and slow by nature.

Two facts shape the choice. First, effaced does not own its database I/O — it runs on a caller-provided session, so the caller's app dictates the session type, and sync `Session` is the universal denominator (`unasync`-style dual codegen is built for libraries that own their I/O clients, e.g. urllib3). Second, export/erasure/consent are rare admin-path operations where event-loop blocking is solved by one documented line; async DB access is not a performance win, it is only a requirement *inside* async frameworks (per the SQLAlchemy maintainers), and SQLAlchemy's own asyncio extension is itself a facade over a sync core.

## Decision

**Sync-first.** Concretely:

- Engine methods (`Exporter.export_subject`, `ErasurePlanner.erase_subject`, `ConsentLedger.record/status/history`) are sync `def` taking the caller's open `Session` as the first positional parameter (typed under `TYPE_CHECKING` in core — ADR-independent rule that core never imports SQLAlchemy at runtime).
- `AuditSink` is sync: `append` runs inside the erasure/consent transaction path. An async external sink, if ever needed, is an additive separate adapter — never a protocol change. (This is the one-time pre-0.1 protocol signature change issue #9 explicitly allows.)
- `async def` is permitted **only** on `Resolver` protocol methods and `SagaRunner.run_once` — the inherently-async edges.
- Components operating outside a caller transaction (`DatabaseAuditSink`, `Outbox.claim_batch`) take a `sessionmaker` at construction; `Outbox.enqueue` keeps using the caller's session so entries commit atomically with the local erasure.
- **Bridging seam:** the Exporter drives async resolvers via `asyncio.run` + `asyncio.gather` inside one internal helper — the only loop ownership in core. Calling it on an event-loop thread raises `RuntimeError`; that is the documented misuse (use a threadpool, below).
- **Resolver contract:** implementations must not bind event-loop-affine resources (async HTTP clients) at construction — create them inside the call. Resolver methods may be driven from different event loops (exporter's fresh loop vs. the saga runner's).
- `SagaRunner.run_once` stays `async def` (awaits resolvers concurrently) but makes blocking DB calls — it is documented as worker/cron-driven, never called on a serving event loop.

### FastAPI story

```python
from fastapi.concurrency import run_in_threadpool

@app.get("/me/export")
async def export_me() -> dict[str, object]:
    bundle = await run_in_threadpool(
        exporter.export_subject, get_session(), "current-user-id"
    )
    return bundle.model_dump(mode="json")
```

Or simpler: declare the route as plain `def` — FastAPI runs sync routes in its threadpool automatically. Apps holding an `AsyncSession` can also call DB-only operations (consent, audit) via `await async_session.run_sync(...)`; that path does **not** work for `export_subject` with resolvers registered (its internal `asyncio.run` would raise inside the loop-thread greenlet) — use the threadpool there.

## Consequences

- Sync apps are first-class with zero extra configuration; async apps pay one documented line per call site.
- A future async facade (e.g. `AsyncExporter` via SQLAlchemy's greenlet bridge — the same architecture SQLAlchemy uses for its own async API) is an additive MINOR change; retreating from async-native would have been MAJOR.
- `run_once` blocks briefly on DB calls between awaits — acceptable in its worker context, wrong on a serving loop.
- Resolvers re-create async clients per call instead of caching them; connection pooling, if it matters, lives inside the resolver behind a loop-safe construct.
