# effaced

Every SaaS eventually has to let a user export their data, delete their account, and prove consent was given. Hand-rolled versions are almost always wrong in the same ways: they miss PII in related tables and third-party systems, they hard-delete legally retained records, and they keep no defensible record of any of it.

**effaced** ships correct, tested mechanisms for the GDPR data-subject rights — export (Art. 15), erasure (Art. 17), consent (Art. 7), and an append-only audit trail (Art. 5(2)) — across your own database **and** the external systems you actually use (Stripe first; more resolvers demand-pulled).

**We ship the mechanisms. You own the compliance.**

[![CI](https://github.com/jaylann/effaced/actions/workflows/ci.yml/badge.svg)](https://github.com/jaylann/effaced/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/effaced)](https://pypi.org/project/effaced/)
[![Python](https://img.shields.io/pypi/pyversions/effaced)](https://pypi.org/project/effaced/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/jaylann/effaced/badge)](https://scorecard.dev/viewer/?uri=github.com/jaylann/effaced)

## 30-second quickstart

```bash
uv add effaced effaced-stripe
```

Annotate the models you already have — the annotations *are* the data map; there is no separate config file to drift out of sync:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from effaced import ErasureStrategy, PiiCategory, RetentionPolicy, pii, subject_link

class User(Base):
    __tablename__ = "users"
    __table_args__ = {"info": subject_link("")}          # this IS the data subject

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(info=pii(PiiCategory.CONTACT))

class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = {"info": subject_link("user")}      # reaches the subject via .user

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
    ConsentLedger, DatabaseAuditSink, ErasurePlanner, Exporter,
    ResolverRegistry, SubjectRef, bind_tables, collect_data_map,
    resolve_subject_graph,
)
from effaced_stripe import StripeResolver

data_map = collect_data_map(Base.metadata)
graph = resolve_subject_graph(data_map, Base.registry)
tables = bind_tables(Base.metadata)        # effaced-owned tables ride your migrations
audit = DatabaseAuditSink(session_factory, tables.audit_events)
registry = ResolverRegistry()
registry.register(StripeResolver(api_key="rk_live_..."))   # explicit — the registry doubles
                                                           # as your "where is my PII" list
stripe_ref = SubjectRef(kind="stripe", value=stripe_customer_id)  # kind == resolver name
ConsentLedger(tables.consent_records, audit).record(session, record)  # Art. 7 — withdraw == grant
Exporter(data_map, graph, Base.metadata, audit, registry).export_subject(
    session, user_id, refs=(stripe_ref,)
)                                                                      # Art. 15
ErasurePlanner(data_map, graph, registry).erase_subject(session, user_id)  # Art. 17
```

Everything else — FK-safe ordering, anonymize-vs-delete, the durable outbox for external calls, retries, idempotency, the audit trail — is bookkeeping effaced does between those calls.

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

The runner half is one call — `await SagaRunner(...).run_once()` — driven by whatever you already operate: a worker process, a cron job, or a FastAPI background thread ([wiring examples](docs/runbooks/saga-runner-wiring.md)). Failures retry on an exponential backoff; an entry that keeps failing is **abandoned loudly** (audited, surfaced for operators — never silently dropped), and `ERASURE_COMPLETED` lands in the audit trail when a subject's last external call succeeds. Concurrent runners are safe: claiming uses `FOR UPDATE SKIP LOCKED`, and a crashed runner's claims heal via a lease (ADR 0010).

## Why not …

| Alternative | The gap |
|---|---|
| **Roll your own** | Misses PII in related tables, logs, and third parties; deletes retained invoices (or retains everything); no Art. 5(2) record; breaks mid-flight when an API is down. |
| **django-gdpr-assist** (closest prior art) | Archived since ~2022, Django-only, local ORM only — no concept of PII in external systems. effaced is the maintained successor for the modern Python stack. |
| **OneTrust / Transcend / DSR platforms** | Heavy, expensive, DPO-facing SaaS — not a drop-in developer library. |
| **GDPR boilerplates** | Shallow download/delete buttons in a template, not reusable machinery with an audit trail. |

## What effaced is not

- **Not legal advice and not a compliance guarantee.** effaced gives you correct machinery to implement Articles 15, 17, 7, and 30 — and an auditable record that you did. Whether your processing is lawful is a legal determination only you (and your counsel) can make.
- **Not able to find data you never declared.** If a model isn't annotated, its data isn't exported or erased. effaced makes that responsibility visible (a completeness linter is on the roadmap) instead of pretending to eliminate it.
- **Not a cookie-consent CMP, not analytics, not a hosted database.**

## Packages

| Package | What | PyPI |
|---|---|---|
| [`effaced`](packages/effaced) | Core: annotations, manifest, export, erasure, consent, audit, saga, resolver interface | `uv add effaced` |
| [`effaced-stripe`](packages/effaced-stripe) | First-party Stripe resolver | `uv add effaced-stripe` |

Write your own resolver by implementing the small [`Resolver` protocol](packages/effaced/src/effaced/resolvers/base.py) — it is public API with the strictest stability promise in the library.

## Status

Pre-release (0.x). The 0.x window is being used to get the manifest format and resolver interface right; 1.0 ships when those are stable enough to support for a year. Built library-shaped from day one and dogfooded in production (VoroAI) before launch.

**SemVer, widened:** API changes, manifest-format changes, *and any change to what gets deleted or exported* are MAJOR — silently changing compliance behaviour is the worst possible failure for a library like this.

## Contributing & development

See [CONTRIBUTING.md](CONTRIBUTING.md). TL;DR: `uv sync && just check && just test`, Conventional Commits, DCO sign-off (`git commit -s`), PRs target `stage`.

## License

[Apache-2.0](LICENSE) © [Justin Lanfermann](https://lanfermann.dev)
