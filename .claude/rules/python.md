---
paths: ["**/*.py"]
---

# Python standards

## Typing — strict, everywhere
- mypy runs `--strict` with the pydantic plugin (`disallow_any_unimported`, `strict_equality`, `extra_checks` on top). It must stay at zero errors.
- No `Any` where a real type exists. `object` for "truly anything JSON-ish" beats `Any`.
- `# type: ignore` requires the error code AND an inline reason: `# type: ignore[type-arg]  # sessionmaker generic unbound here`.
- Untyped SDKs (boto3) can't hide behind `ignore_missing_imports` — `disallow_any_unimported` rejects every annotation using them. Pattern: stub package (`types-boto3[s3]`) in the root dev group, `TYPE_CHECKING`-only imports of its type_defs, and a hand-rolled `Protocol` for the client subset (the stubbed client satisfies it structurally — see `effaced_s3/object_client.py`; AWS CamelCase kwargs get a per-file `N803` ignore).
- Ruff `ANN` rules enforce annotations syntactically; don't suppress them outside tests.
- `from __future__ import annotations` at the top of every module.
- `session.execute(<DML>)` is typed `Result[Any]`, which has no `rowcount`; `cast("CursorResult[Any]", ...)` (TYPE_CHECKING import) to read it.

## Pydantic-first data modeling
- Domain data objects are pydantic `BaseModel` with `model_config = ConfigDict(frozen=True, extra="forbid")`.
- Cross-field invariants live in `model_validator(mode="after")`; single-field constraints in `Field(...)` (e.g. `min_length=1`). Never validate in call sites.
- Never use `model_construct()` in production code — it bypasses validators.
- Enums are `StrEnum` so they serialize as their value.

## File & folder architecture — small and searchable
- **One concept per file.** The file is named after the class it holds, read with the package as namespace: `pii_spec.py` → `PiiSpec`, `data_map.py` → `DataMap` — and inside a domain package the prefix drops: `erasure/result.py` → `ErasureResult`, `consent/record.py` → `ConsentRecord` (never re-duplicate the package name into the file name).
- A new class/protocol/enum of public relevance gets its own file, re-exported from the package `__init__.py` (which holds docstring + re-exports ONLY, no logic).
- Hard cap **600 lines per source file**, enforced by `scripts/check_file_length.py` in `just check` and CI. If you approach it, split into a package.
- Prefer packages over modules: `erasure/{plan,planner,result}.py`, not one `erasure.py`.
- Absolute imports only (ruff TID bans relative imports).
- Complexity budgets are CI-enforced (mccabe ≤ 10, pylint max-args 6 / branches 10 / statements 40). Don't suppress; restructure.

## Sync/async boundaries (ADR 0006)
- The public engine API is sync `def`. The caller's open `Session` is the first positional parameter; never create sessions for caller-facing operations. Components operating outside a caller transaction (`DatabaseAuditSink`, `Outbox.claim_batch`) take a `sessionmaker` at construction instead.
- `async def` is permitted ONLY on `Resolver` protocol methods and `SagaRunner.run_once`. Adding async anywhere else needs an ADR.
- Bridging: the sync core drives async resolvers exclusively through `asyncio.run` in one internal helper; never call `asyncio.run` where a loop may already be running. Async web frameworks consume the engines via `run_in_threadpool` (or plain `def` routes); `AsyncSession` apps may use `session.run_sync(...)` for DB-only operations.
- Resolvers must not cache loop-bound async clients in `__init__` — create them inside the call.
- When `asyncio.run` refuses to start (already on a loop thread) it leaves the passed coroutine unconsumed — `close()` it before re-raising or pytest reports a `RuntimeWarning: coroutine ... was never awaited`.

## Misc
- Docstrings: Google convention on every public module/class/function (ruff `D`). They are the future generated docs site — write them as documentation.
- Core (`effaced/*` except `adapters/`) must not import SQLAlchemy or any storage library (semgrep-gated); storage-specific code lives in `effaced/adapters/<stack>/`. Core can still issue SQL by building statements off the bound `Table` handle (`table.select()`, `.update()`, `c.col.in_(...)`, `.with_for_update(skip_locked=True)`) — no runtime import needed. `Select.with_only_columns()` recalculates the FROM list from the new columns plus later `.where()` criteria, so selecting a hop-chain alias's column (including the subject-id path the retention sweeper uses) off `table.select()` yields the implicit join without a `select()` import.
- The PostToolUse hook auto-formats edited files and auto-removes imports that are momentarily unused, including when you edit a different region of the same file; imports added in one Edit and used in a later Edit can be stripped in between. Add usages with (or before) their imports, and fix imports last when extending an existing module.
