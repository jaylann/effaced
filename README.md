<div align="center">

# effaced

**GDPR data-subject mechanisms for the modern Python stack.**

Export · Erasure · Consent · Audit — across your database **and** the external systems you use.

**We ship the mechanisms. You own the compliance.**

[![CI](https://github.com/jaylann/effaced/actions/workflows/ci.yml/badge.svg)](https://github.com/jaylann/effaced/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/jaylann/effaced/badge)](https://scorecard.dev/viewer/?uri=github.com/jaylann/effaced)

**[Docs](https://jaylann.github.io/effaced/)** · [Quickstart](https://jaylann.github.io/effaced/docs/quickstart/) · [Example app](examples/fastapi-quickstart) · [Why effaced?](#why-not-)

</div>

---

Every SaaS eventually has to let a user export their data, delete their account, and prove consent was given. Hand-rolled versions are almost always wrong in the same ways: they miss PII in related tables and third-party systems, they hard-delete legally retained records, and they keep no defensible record of any of it.

**effaced** ships correct, tested mechanisms for the GDPR data-subject rights — across your own database and the external systems you actually use (Stripe first; more resolvers demand-pulled).

## What you get

| Right | Article | Mechanism |
|---|---|---|
| Export | Art. 15 | `Exporter` — full subject bundle, including legally retained fields and external systems |
| Erasure | Art. 17 | `ErasurePlanner` — FK-safe delete/anonymize, retention-aware, durable saga for external calls |
| Consent | Art. 7 | `ConsentLedger` — withdrawal as easy as grant, by construction |
| Accountability | Art. 5(2) | `DatabaseAuditSink` — append-only audit trail, no PII in events |
| External systems | — | `Resolver` protocol + first-party `StripeResolver` |

## 30-second quickstart

> **Not on PyPI yet.** Until 0.1.0 ships, install both packages straight from this repo:

```bash
uv add "effaced @ git+https://github.com/jaylann/effaced#subdirectory=packages/effaced" \
       "effaced-stripe @ git+https://github.com/jaylann/effaced#subdirectory=packages/effaced-stripe"
```

Annotate the models you already have — the annotations *are* the data map; there is no separate config file to drift out of sync:

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from effaced import ErasureStrategy, PiiCategory, RetentionPolicy, pii, subject_link

class Base(DeclarativeBase): ...

class User(Base):
    __tablename__ = "users"
    __table_args__ = {"info": subject_link("")}          # this IS the data subject

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(info=pii(PiiCategory.CONTACT))

class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = {"info": subject_link("user")}      # reaches the subject via .user

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user: Mapped[User] = relationship()
    billing_address: Mapped[str] = mapped_column(
        info=pii(
            PiiCategory.FINANCIAL,
            erasure=ErasureStrategy.RETAIN,              # legally retained — never deleted,
            retention=RetentionPolicy(reason="§147 AO"), # and the audit trail says why
        )
    )
```

Then the entire integration surface is three calls:

```python
from effaced import (
    ConsentLedger, DatabaseAuditSink, ErasureExecutor, ErasurePlanner,
    Exporter, Outbox, ResolverRegistry, SubjectRef, bind_tables,
    collect_data_map, resolve_subject_graph,
)
from effaced_stripe import StripeResolver

data_map = collect_data_map(Base.metadata)
graph = resolve_subject_graph(data_map, Base.registry)
tables = bind_tables(Base.metadata)        # effaced-owned tables ride your migrations
audit = DatabaseAuditSink(session_factory, tables.audit_events)
outbox = Outbox(session_factory, tables.outbox)
registry = ResolverRegistry()
registry.register(StripeResolver(api_key="rk_restricted_..."))  # explicit — the registry doubles
                                                                 # as your "where is my PII" list
stripe_ref = SubjectRef(kind="stripe", value=stripe_customer_id)  # kind == resolver name

ConsentLedger(tables.consent_records, audit).record(session, record)  # Art. 7 — withdraw == grant
Exporter(data_map, graph, Base.metadata, audit, registry).export_subject(
    session, user_id, refs=(stripe_ref,)
)                                                                      # Art. 15
ErasurePlanner(
    data_map, graph, registry,
    executor=ErasureExecutor(Base.metadata), outbox=outbox, audit_sink=audit,
).erase_subject(session, user_id, refs=(stripe_ref,))                 # Art. 17
```

Everything else — FK-safe ordering, anonymize-vs-delete, the durable outbox for external calls, retries, idempotency, the audit trail — is bookkeeping effaced does between those calls. A runnable end-to-end version (FastAPI + local Postgres) lives in [examples/fastapi-quickstart](examples/fastapi-quickstart).

## How erasure actually works

Erasure is a **saga, not a function call**. The local deletion runs in one atomic transaction; external API calls (which cannot join that transaction) are enqueued durably *in the same transaction* and fanned out afterwards with retries and idempotency. When the Stripe API is down mid-deletion, the system is in a known, recorded state — not a half-erased mystery.

```
erase_subject(...)
 ├── one atomic DB transaction
 │    ├── delete / anonymize in FK-safe order
 │    ├── skip + record legally retained fields
 │    └── enqueue outbox entries for external systems
 └── saga runner (your worker/cron)
      ├── Stripe: delete customer  ── retry w/ backoff, "already gone" = success
      └── audit trail records every outcome, including abandonment
```

The runner half is one call — `await SagaRunner(...).run_once()` — driven by whatever you already operate: a worker process, a cron job, or a FastAPI background thread ([wiring guide](https://jaylann.github.io/effaced/docs/guides/saga-runner-wiring/), [operator runbook](docs/runbooks/saga-runner-wiring.md)). Failures retry on an exponential backoff; an entry that keeps failing is **abandoned loudly** (audited, surfaced for operators — never silently dropped), and `ERASURE_COMPLETED` lands in the audit trail when a subject's last external call succeeds. Concurrent runners are safe: claiming uses `FOR UPDATE SKIP LOCKED`, and a crashed runner's claims heal via a lease (ADR 0010).

## Documentation

Full docs live at **[jaylann.github.io/effaced](https://jaylann.github.io/effaced/)** — the API reference is generated from the same docstrings you'll read in this repo.

| | |
|---|---|
| [Quickstart](https://jaylann.github.io/effaced/docs/quickstart/) | Annotate, wire, run all three rights end to end |
| [Concepts](https://jaylann.github.io/effaced/docs/concepts/annotations/) | Annotations, manifest, export, erasure, saga, consent, audit, resolvers |
| [Guides](https://jaylann.github.io/effaced/docs/guides/stripe/) | Stripe resolver, saga-runner wiring, audit hardening |
| [API reference](https://jaylann.github.io/effaced/docs/reference/) | Generated from docstrings, fully typed |
| [`examples/fastapi-quickstart`](examples/fastapi-quickstart) | Runnable FastAPI app exercising consent, export, and erasure |
| [`docs/runbooks/`](docs/runbooks) | Operator runbooks (saga wiring, audit hardening, release) |
| [`docs/decisions/`](docs/decisions) | Architecture decision records |

## Packages

| Package | What | Install |
|---|---|---|
| [`effaced`](packages/effaced) | Core: annotations, manifest, export, erasure, consent, audit, saga, resolver interface | `uv add effaced` (once 0.1.0 is on PyPI — from git until then, see quickstart) |
| [`effaced-stripe`](packages/effaced-stripe) | First-party Stripe resolver | `uv add effaced-stripe` (same) |

Write your own resolver by implementing the small [`Resolver` protocol](packages/effaced/src/effaced/resolvers/base.py) — it is public API with the strictest stability promise in the library.

## Why not …

| Alternative | The gap |
|---|---|
| **Roll your own** | Misses PII in related tables, logs, and third parties; deletes retained invoices (or retains everything); no Art. 5(2) record; breaks mid-flight when an API is down. |
| **django-gdpr-assist** (closest prior art) | Upstream repo archived (last release April 2022); Django-only, local ORM only — no concept of PII in external systems. effaced covers the same ground for SQLAlchemy stacks and extends it to external systems. |
| **OneTrust / Transcend / DSR platforms** | Heavy, expensive, DPO-facing SaaS — not a drop-in developer library. |
| **GDPR boilerplates** | Shallow download/delete buttons in a template, not reusable machinery with an audit trail. |

## What effaced is not

- **Not legal advice and not a compliance guarantee.** effaced gives you correct machinery to implement Articles 15, 17, 7, and 30 — and an auditable record that you did. Whether your processing is lawful is a legal determination only you (and your counsel) can make.
- **Not able to find data you never declared.** If a model isn't annotated, its data isn't exported or erased. effaced makes that responsibility visible (a completeness linter is on the roadmap) instead of pretending to eliminate it.
- **Not a cookie-consent CMP, not analytics, not a hosted database.**

## Status & stability

Pre-release (0.x), not yet on PyPI. The 0.x window is being used to get the manifest format and resolver interface right — and to dogfood effaced in production before 1.0; 1.0 ships when both have survived that.

**SemVer, widened:** API changes, manifest-format changes, *and any change to what gets deleted or exported* are MAJOR — silently changing compliance behaviour is the worst possible failure for a library like this.

**Evidence, not claims:** [PROOFS.md](PROOFS.md) maps every published guarantee — no cross-subject bleed, retained-category preservation, idempotent convergence, audited fault outcomes — to the property, unit, and Postgres tests that prove it, including a fault-injection matrix over the erasure pipeline.

## Contributing & development

See [CONTRIBUTING.md](CONTRIBUTING.md). TL;DR: `just bootstrap`, then `just check && just test`; Conventional Commits, DCO sign-off (`git commit -s`), PRs target `stage`.

## License

[Apache-2.0](LICENSE) © [Justin Lanfermann](https://lanfermann.dev)
