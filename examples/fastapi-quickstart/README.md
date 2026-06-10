# FastAPI quickstart

The smallest complete integration: annotated models (`models.py`) and the
three trigger points (`app.py`) — record consent, export a subject, erase a
subject. Everything else is bookkeeping effaced does between those calls.

```bash
uv add effaced effaced-stripe fastapi uvicorn
uvicorn app:app --reload
```

> The example wires `get_session()` as a placeholder — plug in your real
> SQLAlchemy session dependency.

The effaced engines are sync by design — async routes dispatch them via
`run_in_threadpool`, plain `def` routes call them directly (FastAPI
threadpools sync routes automatically). Rationale and the full
integration story:
[ADR 0006](../../docs/decisions/0006-session-strategy.md).
