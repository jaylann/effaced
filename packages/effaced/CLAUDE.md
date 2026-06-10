# effaced (core package)

Storage-agnostic core. **No module outside `adapters/` may import SQLAlchemy or any storage library.**

## Module map (one concept per file; file = class)

| Package | Holds | Key invariant |
|---|---|---|
| `categories/` | `PiiCategory`, `LegalBasis`, `ErasureStrategy` enums | members are manifest format — removal/rename = MAJOR |
| `annotations/` | `PiiSpec`, `RetentionPolicy`, `SubjectLink`, `SubjectRef` (frozen pydantic) | `RETAIN` requires a `RetentionPolicy` (validator) |
| `manifest/` | `DataMap`, `TableEntry`, `ColumnEntry`, `migration.py` | format change ⇒ bump `MANIFEST_SCHEMA_VERSION` + migration branch; old payloads never rejected |
| `adapters/sqlalchemy/` | `pii()`/`subject_link()` info-dict helpers, `collect_data_map()`, `storage/` (`bind_tables()` → `EffacedTables`: the effaced-owned `effaced_*` tables) | the ONLY SQLAlchemy-aware code |
| `export/` | `Exporter`, `ExportBundle`, `ExportRecord` | failures land in `incomplete_sources`, never silent omission |
| `erasure/` | `ErasurePlanner`, `ErasurePlan`/`ErasureStep`, `ErasureResult` | plans are inspectable before execution; local steps atomic, external via outbox |
| `consent/` | `ConsentLedger`, `ConsentRecord` | records are immutable events; status is derived, never stored |
| `audit/` | `AuditEvent(+Type)`, `AuditSink` protocol, `DatabaseAuditSink` | append-only by construction; payloads are small scalars, never rich PII |
| `resolvers/` | `Resolver` protocol, `ResolverRegistry`, result models | public API, additive-only; explicit registration, no discovery |
| `saga/` | `Outbox`, `OutboxEntry(+Status)`, `SagaRunner` | entries enqueue in the SAME transaction as local erasure; `entry_id` is the idempotency key; `claim_batch` uses the constructor's `session_factory` |

`__init__.py` files: docstring + re-exports only. New public class ⇒ new file ⇒ re-export ⇒ add to root `__all__` (test_public_api guards it).

## When implementing engine logic (currently NotImplementedError)

- Keep signatures — they are the published API surface; changing them is breaking. The sync/async shape is settled by ADR 0006 (sync engine `def`s taking the caller's `Session`; async only on `Resolver` methods and `SagaRunner.run_once`).
- Every outcome (success, failure, retention skip, abandonment) emits an audit event.
- FK-safe ordering comes from metadata, never from user-supplied order.
- Add bleed/retention/idempotency property tests alongside (see `.claude/rules/testing.md`).
