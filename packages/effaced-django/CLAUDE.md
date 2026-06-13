# effaced-django (Django adapter)

The second ORM adapter (issue #62). Proves the core is storage-agnostic.

## Design (Design A)

- Django has no per-column `info` slot, so PII is declared on a nested `Effaced` class
  + the `@effaced_model` decorator, collected into an `AnnotationRegistry`.
- `introspection.build_metadata` translates `Model._meta` into an effaced-annotated
  SQLAlchemy `MetaData` (types via `_TYPE_MAP`, FK constraints, `info` dicts). No live
  DB connection — reads declared field metadata only.
- The subject graph is resolved from FK constraints via
  `effaced.resolve_subject_graph_from_fk` (added to core for this adapter); no ORM
  mappers. **Subject-link paths name target tables, not relationship attributes.**
- Execution reuses the unchanged SQLAlchemy executors (ADR 0006: the sync `Session` is
  the universal substrate). `from_models` takes a `session_factory`, exactly like
  `EffacedStack.from_base`.

## Invariants

- The FK resolver produces a graph identical in shape/guarantees to the ORM resolver
  (parity test in core `test_resolve_subject_graph_from_fk.py`).
- An unmapped Django field type raises `EffacedDjangoError` — never a silent guess.
- Owned-table native Django migrations are a follow-up; today the owned tables ride the
  derived `MetaData` (`create_all` / caller migration).

## Learnings

- `__table_args__` as a bare dict is read as Table kwargs; table-level `info` must be
  `__table_args__ = {"info": subject_link(...)}` (cost a red test).
