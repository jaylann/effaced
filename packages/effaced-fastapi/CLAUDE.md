# effaced-fastapi

FastAPI integration layer (ADR 0020). Depends on `effaced` (workspace source) + `fastapi` + `sqlalchemy`. FastAPI imports live ONLY in this package — never in core.

- `EffacedFastAPI` wires `effaced.EffacedStack` (or adopts a prewired one via `stack=`) and exposes `router(subject=...)` + `lifespan()`. `integration.py` is a sanctioned deviation from file-=-class naming (`effaced_fastapi.py` would duplicate the package name).
- **Routes are plain `def`** — the sync engines run on FastAPI's threadpool (ADR 0006). The subject provider may be `async`; a `def` route depending on an `async def` dependency is supported FastAPI behaviour and pinned by `test_async_subject_provider.py`. The only `async def` in the package is framework-boundary glue (the lifespan contextmanager, the worker's private drain loop) — sanctioned by ADR 0020, never engine API.
- **Route paths and response shapes are public API.** Responses are the engines' own result models (`ExportBundle`, `ErasureResult`, `ConsentRecord`, `RestrictionRecord`) — never invent endpoint-specific shapes that restate erasure/export semantics. Changing a path, default, or response model is MAJOR (widened SemVer).
- Subject identity is the app's: the `Subject(subject_id, refs)` dependency decides who the subject is and which external refs they carry. The router never authenticates, never guesses refs.
- No rectification endpoint by design (ADR 0020): which corrections a subject may self-serve is an app-level authorization decision. Don't add one without an allowlist design + ADR.
- `SagaWorker` is a daemon **thread** running its own `asyncio.run` loop (the saga-runner-wiring runbook pattern). NEVER `asyncio.create_task(run_once())` on the serving loop — `run_once` blocks on the database between awaits.
- `session_dependency` is built once in `__init__` so `app.dependency_overrides[gdpr.session_dependency]` works by identity. Default: one transaction per request via `session_factory.begin()`.
- Tests: sqlite `StaticPool` + `check_same_thread=False` (dependency and route may run on different threadpool threads) and a `RecordingAuditSink`-style fake (the default `DatabaseAuditSink` opens a second connection — SQLite's single writer blocks on it).
