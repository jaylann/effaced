# Hardening the audit trail: append-only at the database

`effaced_audit_events` is append-only **by construction** in the library:
no code path updates or deletes rows, and the `AuditSink` protocol exposes
no surface to do so. The table's schema alone, however, cannot stop raw SQL
or another application from rewriting history.

If your threat model includes writers outside effaced, add a Postgres
trigger that rejects mutation at the database itself. This is optional
hardening of the mechanism — not a compliance determination.

These are complementary, defence-in-depth layers:

- The **hash chain** (ADR 0028, computed by default in `DatabaseAuditSink`)
  *detects* that a recorded row was modified out of band — each row hashes
  its content chained to the prior row's hash, and
  `AuditChainVerifier(...).verify()` recomputes the chain and reports the
  first break. It does **not** prevent modification: a writer with table
  access can recompute the chain forward from the row they edit.
- The **trigger** below *rejects* `UPDATE`/`DELETE` at the database, so the
  mutation never lands. It can be dropped by a table owner or superuser.

Run both: the trigger raises the bar to rewriting history, the chain makes a
modification that gets past it (or past a dropped trigger) detectable.
Verification reads the trail only and writes nothing.

## Trigger

```sql
CREATE FUNCTION effaced_audit_events_no_rewrite() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'effaced_audit_events is append-only (% blocked)', TG_OP;
END;
$$;

CREATE TRIGGER effaced_audit_events_append_only
    BEFORE UPDATE OR DELETE ON effaced_audit_events
    FOR EACH ROW EXECUTE FUNCTION effaced_audit_events_no_rewrite();
```

## Shipping it with Alembic

```python
def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION effaced_audit_events_no_rewrite() ...
    """)  # full SQL from above


def downgrade() -> None:
    op.execute("DROP TRIGGER effaced_audit_events_append_only ON effaced_audit_events")
    op.execute("DROP FUNCTION effaced_audit_events_no_rewrite()")
```

## Notes

- The trigger blocks `UPDATE`/`DELETE` for every role, including the
  application's own. Table owners and superusers can still drop the
  trigger — combine with restricted grants if that matters to you.
- `TRUNCATE` is not covered by row-level triggers; revoke it explicitly
  (`REVOKE TRUNCATE ON effaced_audit_events FROM your_app_role`).
- Retention of the trail itself (e.g. pruning very old events) then
  requires deliberately dropping the trigger in a migration — which is
  exactly the kind of explicit, reviewable step you want.
- The hash chain links each row to the one before it. The per-append
  transaction is the serialization point; under genuinely concurrent
  appenders two rows may legitimately chain to the same predecessor, which
  `AuditChainVerifier` reports as a fork (surfaced, never silently healed).
  The single-writer erasure/consent path is already linear; deployments that
  want a strictly linear chain serialize their audit writes (one writer, or a
  Postgres advisory lock around `append`).
- Rows written before this release, or by a custom sink that does not compute
  the chain, carry `NULL` hashes; `AuditChainVerifier` treats them as an
  unchained prefix and verifies from the first row that has a hash — a `NULL`
  hash is absence of evidence, never evidence of tampering.
