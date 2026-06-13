# effaced-django

Django ORM adapter for [effaced](https://github.com/jaylann/effaced) — the second
storage adapter alongside the built-in SQLAlchemy one, and the proof that effaced's
core is genuinely storage-agnostic.

**effaced ships mechanisms, never compliance determinations.**

## What it does

Author your PII map on Django models, then run effaced's Article 15 export, Article 17
erasure, Article 16 rectification, consent, restriction, and retention engines against
your Django database.

```python
from django.db import models
from effaced import PiiCategory
from effaced_django import effaced_model, pii, subject_link

@effaced_model(subject_link(""))            # the data subject
class User(models.Model):
    email = models.EmailField()
    class Meta:
        db_table = "users"
    class Effaced:
        email = pii(PiiCategory.CONTACT)

@effaced_model(subject_link("users"))       # reaches the subject via its FK to users
class Post(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    body = models.TextField()
    class Meta:
        db_table = "posts"
    class Effaced:
        body = pii(PiiCategory.BEHAVIORAL)
```

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from effaced_django import DjangoEffacedStack

engine = create_engine("postgresql+psycopg://…")   # the same database Django uses
stack = DjangoEffacedStack.from_models(sessionmaker(engine))

with sessionmaker(engine).begin() as session:
    stack.planner.erase_subject(session, subject_id="42")
```

## How it works (Design)

Django models carry no per-column metadata slot, so declarations live on a nested
`Effaced` class and the `@effaced_model` decorator. The adapter translates
`Model._meta` into an effaced-annotated SQLAlchemy `MetaData` — columns, types,
primary keys, and **foreign-key constraints** — then:

- collects the manifest with the core `collect_data_map`, and
- resolves the subject graph from those foreign keys with
  `effaced.resolve_subject_graph_from_fk` (no ORM mappers required).

Execution then reuses effaced's **existing SQLAlchemy executors** on that metadata,
so erasure/export/audit semantics are byte-identical to the SQLAlchemy adapter (ADR
0006 makes the SQLAlchemy `Session` the universal substrate; Django is a consumer of
it). Subject-link paths name the **target tables** of each foreign-key hop (e.g.
`subject_link("posts.users")` two hops out), because resolution walks FK constraints
rather than ORM relationship names.

## Transactions

`DjangoEffacedStack.from_models` takes a SQLAlchemy `session_factory` bound to the same
database Django uses. Run erasure inside a `django.db.transaction.atomic()` block (and a
matching SQLAlchemy transaction) so the outbox enqueue shares the transaction with the
local deletes — anything else reintroduces the half-erased state effaced exists to
prevent (ADR 0010).

## Owned tables

The four `effaced_*` owned tables are mounted onto the derived `MetaData`; materialize
them with `metadata.create_all(engine)` or a caller migration. **Native Django
migrations for the owned tables are a planned follow-up.**

## Status

Pre-alpha. Field-type coverage spans the common Django fields; an unmapped field type
fails loudly (`EffacedDjangoError`) rather than guessing. Composite/compound subject
keys are not supported (single subject-id column).
