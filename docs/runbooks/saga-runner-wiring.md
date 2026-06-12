# Wiring the saga runner

`SagaRunner.run_once()` claims one batch of due outbox entries, fans the
resolver calls out concurrently, and books every outcome (ADR 0010). It owns
no event loop and no schedule — you drive it with whatever your application
already has. One rule, from ADR 0006: **never run it on a serving event
loop** — `run_once` makes blocking database calls between awaits and would
stall request handling.

Running several drivers at once is safe: claiming uses `FOR UPDATE SKIP
LOCKED`, and a crashed runner's claims are re-claimed after the lease
expires.

## Shared wiring

```python
from sqlalchemy.orm import sessionmaker

from effaced import (
    BackoffPolicy, DatabaseAuditSink, Outbox, OutboxOperation,
    ResolverRegistry, SagaRunner, bind_tables,
)

session_factory = sessionmaker(engine)
tables = bind_tables(metadata)

registry = ResolverRegistry()
registry.register(StripeResolver(api_key="rk_live_..."))

audit = DatabaseAuditSink(session_factory, tables.audit_events)
# Pass the same audit sink to the outbox so `requeue()` (below) can append
# its supervised event — `requeue` raises `ConfigurationError` without one.
outbox = Outbox(session_factory, tables.outbox, audit_sink=audit)

runner = SagaRunner(
    registry,
    outbox,
    audit,
    # Size the lease above your slowest resolver call: an expired lease
    # mid-call means double execution (idempotent, but wasteful).
    backoff=BackoffPolicy(),
)
```

Connection budget: at the moment a subject completes, the runner holds the
outbox transaction *and* opens a second connection for the audit append —
size the engine's pool for **two connections per concurrent runner thread**.
An exhausted pool only times out and retries via the lease, but that is
wasted work.

## FastAPI: a background thread, not a background task

A daemon thread with its own `asyncio.run` keeps the runner's blocking
database calls off the serving loop. (`asyncio.create_task(...)` on the app's
loop is exactly what ADR 0006 forbids.)

```python
import asyncio
import threading
from contextlib import asynccontextmanager

stop = threading.Event()

def drain_forever() -> None:
    async def loop() -> None:
        while not stop.is_set():
            if await runner.run_once() == 0:
                await asyncio.sleep(5.0)  # queue empty — poll gently
    asyncio.run(loop())

@asynccontextmanager
async def lifespan(app):
    worker = threading.Thread(target=drain_forever, daemon=True)
    worker.start()
    yield
    stop.set()
    worker.join(timeout=10)

app = FastAPI(lifespan=lifespan)
```

## Dedicated worker process

```python
# saga_worker.py — run alongside your web processes
import asyncio

async def main() -> None:
    while True:
        if await runner.run_once() == 0:
            await asyncio.sleep(5.0)

if __name__ == "__main__":
    asyncio.run(main())
```

The same shape drops into a Celery/RQ/Huey periodic task: the task body is
`asyncio.run(drain())` where `drain` loops `run_once` until it returns 0.

## Cron

```python
# saga_drain.py — run from cron, e.g. * * * * *
import asyncio

async def drain() -> int:
    total = 0
    while count := await runner.run_once():
        total += count
    return total

if __name__ == "__main__":
    asyncio.run(drain())
```

Overlapping cron invocations are safe — concurrent runners skip each other's
locked rows and never double-claim.

## Operating abandonment

An entry whose retries are exhausted (or whose resolver raised the
non-retryable `ResolverError`) becomes `ABANDONED`: it is never retried, it
blocks `ERASURE_COMPLETED` for its subject permanently, and it is always
audited (`ERASURE_STEP_FAILED` with `abandoned: true`). Abandonment means a
data-subject request is **not finished** — alert on it:

```python
counts = outbox.status_counts()      # one entry per OutboxStatus, zero-filled
if counts[OutboxStatus.ABANDONED]:
    for entry in outbox.list_abandoned():   # full entries, oldest first
        page_someone(entry.subject_id, entry.resolver, entry.last_error)
```

(Or straight SQL, if your monitoring lives there:
`SELECT subject_id, resolver, last_error, attempts FROM effaced_outbox
WHERE status = 'abandoned';`)

Remediation (ADR 0015) — abandoned **erase** entries: fix the underlying cause
(credentials, deleted API resource, resolver bug), then `requeue` them by id.
Each flips back to `PENDING` with a full retry budget (`attempts = 0`,
`next_attempt_at = NULL`) under its unchanged `entry_id`, so the resolver-side
idempotency key holds and re-execution converges:

```python
abandoned = outbox.list_abandoned()       # full entries, oldest first
erase_ids = [item.entry_id for item in abandoned if item.operation is OutboxOperation.ERASE]
# Look at what you are about to re-run, then hand back the ids:
requeued = outbox.requeue(erase_ids)
# `requeued` reports only the entries that actually flipped — ids that were
# already requeued or no longer abandoned are skipped, never errors.
```

`requeue` appends one `ERASURE_REQUEUED` event per flipped entry (carrying the
prior attempt count and the prior error's class name) **before** the row flips,
so the supervised re-run is always in the trail. A requeued entry blocks
`ERASURE_COMPLETED` again until it lands; an operator who requeues without
fixing the cause will see a second abandonment in the trail, each cycle its own
evidence.

Abandoned **rectify** entries cannot be requeued — `requeue` refuses them with
`ConfigurationError`. A rectify entry's corrections (the new values) are real
PII held only in the outbox row's `payload` and are **cleared at abandonment**
(ADR 0013), so there is nothing left to re-apply; a blind requeue would
re-execute with no corrections — a silent no-op that still reports completion.
Remediate by **re-issuing the rectification** through your `Rectifier` (the same
call that first enqueued it), which writes a fresh entry carrying the
corrections again:

```python
# Fix the cause, then re-run the original rectification — NOT requeue():
rectifier.rectify_subject(session, subject_id, corrections)
```

## Operating scheduled expiry

`status_counts()[OutboxStatus.SCHEDULED]` is **pending vendor expiry, not a
fault** (ADR 0018): each entry is an erasure a retention-only system can only
expire, parked until its horizon and then re-claimed to verify. The horizons
live in the audit trail — one `ERASURE_EXPIRY_SCHEDULED` event per park,
carrying `expires_at`. A subject's erasure stays open (no `ERASURE_COMPLETED`)
until every scheduled entry verifies expiry, so don't expect completion before
the vendor's retention window lapses. What does deserve attention: repeated
`ERASURE_EXPIRY_SCHEDULED` events for the same entry — a vendor that keeps
slipping its horizon re-parks loudly instead of completing or abandoning.

The outbox is a mechanism and this is its operating manual — whether an
abandoned erasure needs escalation under your obligations is a determination
your process owns, not the library.
