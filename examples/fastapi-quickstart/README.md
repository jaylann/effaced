# FastAPI quickstart

The smallest complete integration: annotated models (`models.py`) and one
router (`app.py`, via `effaced-fastapi`) exposing the trigger points —
record consent, export a subject, erase a subject. Everything else is
bookkeeping effaced does between those calls.

## Run it

```bash
# 1. A local Postgres (skip if you already have one):
docker run --rm -d --name effaced-demo-pg -p 5432:5432 \
  -e POSTGRES_USER=effaced -e POSTGRES_PASSWORD=effaced -e POSTGRES_DB=effaced \
  postgres:16

# 2. Start the app — inside this repo (deps come from `uv sync --all-packages`):
cd examples/fastapi-quickstart
uv run uvicorn app:app --reload
```

In your own project the install line is (`effaced-fastapi` is unreleased —
take it from git until its first release):

```bash
uv add effaced effaced-stripe uvicorn "psycopg[binary]" \
       "effaced-fastapi @ git+https://github.com/jaylann/effaced#subdirectory=packages/effaced-fastapi"
```

Startup creates the tables (your migrations would normally own that) and
seeds one demo user, so the three trigger points work immediately:

```bash
curl -X POST 'localhost:8000/me/consent' -H 'X-User-Id: 1' \
  -H 'Content-Type: application/json' \
  -d '{"purpose": "newsletter", "granted": true, "policy_version": "2026-06"}'
curl 'localhost:8000/me/export' -H 'X-User-Id: 1'
curl -X DELETE 'localhost:8000/me' -H 'X-User-Id: 1'
```

The `X-User-Id` header stands in for your real auth dependency.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://effaced:effaced@localhost:5432/effaced` | SQLAlchemy URL of your Postgres |
| `STRIPE_API_KEY` | unset | When set, the Stripe resolver is registered (use a restricted key) |
| `STRIPE_CUSTOMER_ID` | unset | When set together with the key, export/erasure also reach Stripe |

Resolvers are registered declaratively from settings via
`registry_from_settings`: the app authors a `ResolverSpec` naming Stripe's
required key, and the helper registers the resolver only when that key is
present. This stays explicit and auditable — there is no auto-discovery —
and `build.outcomes` records what was wired and what was skipped (a good
thing to log at startup).

Without the Stripe variables the example runs fully locally — no network
calls leave your machine. Set **both** or neither: with only the key, the
resolver is registered but no requests carry a Stripe ref, so export and
erasure record Stripe as a skipped resolver instead of reaching it.

## Notes

The routes come from `effaced-fastapi`: plain `def` endpoints, so the
sync engines (by design — [ADR
0006](../../docs/decisions/0006-session-strategy.md)) run on FastAPI's
threadpool, while your auth dependency may be `async`. The integration
layer's shape — what it mounts, what stays yours — is [ADR
0020](../../docs/decisions/0020-fastapi-integration-layer.md).
