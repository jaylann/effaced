# effaced-fastapi

FastAPI integration for [effaced](https://github.com/jaylann/effaced) —
mount a data subject's trigger points as one router instead of wiring
every engine by hand.

- **Consent (Art. 7):** `POST /consent` records grants and withdrawals,
  `GET /consent/{purpose}` answers the current status.
- **Export (Art. 15):** `GET /export` returns the subject's data across
  your database and registered resolvers.
- **Erasure (Art. 17):** `DELETE` at the router prefix erases locally in
  the request transaction and enqueues external erasure for the saga.
- **Restriction (Art. 18, opt-in):** `POST /restriction` and
  `GET /restriction` behind `router(restriction=True)`.

```bash
uv add effaced effaced-fastapi
```

```python
from effaced_fastapi import EffacedFastAPI, Subject

gdpr = EffacedFastAPI(base=Base, session_factory=session_factory,
                      resolvers=[StripeResolver(api_key=...)])

def current_subject(user: Annotated[User, Depends(current_user)]) -> Subject:
    return Subject(subject_id=str(user.id),
                   refs=(SubjectRef(kind="stripe", value=user.stripe_customer_id),))

app = FastAPI(lifespan=gdpr.lifespan())  # optional: drains the outbox in the background
app.include_router(gdpr.router(subject=current_subject), prefix="/me")
```

That's the whole integration: `POST /me/consent`, `GET /me/export`,
`DELETE /me`. Annotating your models with `pii()` / `subject_link()` is
the part that stays yours — see the
[quickstart](https://github.com/jaylann/effaced/tree/main/examples/fastapi-quickstart).

## What the router does — and deliberately doesn't

- **Your auth stays yours.** The `subject` dependency you pass resolves
  who the request is about (`Subject(subject_id, refs)`); the router
  never authenticates and never guesses where a subject lives in
  external systems.
- **Plain `def` routes.** effaced's engines are sync by design (ADR
  0006); FastAPI runs them on its threadpool, so your event loop never
  blocks — your subject provider can still be `async`.
- **Responses are the engines' result models** (`ExportBundle`,
  `ErasureResult`, `ConsentRecord`) — no endpoint-specific shapes, so
  response stability follows effaced's
  [widened SemVer](https://github.com/jaylann/effaced#status--stability).
- **One transaction per request.** The default session dependency wraps
  each request in `session_factory.begin()`; override it per router
  (`router(session=...)`) or globally
  (`app.dependency_overrides[gdpr.session_dependency]`).
- **No rectification endpoint.** Which corrections a subject may
  self-serve is an authorization decision your application owns — call
  `Rectifier.rectify_subject` from your own route (ADR 0020).
- **Background drain included.** `gdpr.lifespan()` runs a `SagaWorker`
  daemon thread that drains the outbox; apps with their own lifespan can
  construct `SagaWorker` directly.

## Custom wiring

Need a custom audit sink or a settings-driven resolver registry? Build
the stack yourself and hand it over:

```python
from effaced import EffacedStack, registry_from_settings

build = registry_from_settings(specs)
stack = EffacedStack.from_base(Base, session_factory, registry=build.registry)
gdpr = EffacedFastAPI(stack=stack)
```

## Not legal advice

effaced ships mechanisms — endpoints, audit trails, idempotent erasure —
not compliance determinations. Whether your use of them satisfies your
obligations is a determination your process owns.
