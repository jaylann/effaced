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
