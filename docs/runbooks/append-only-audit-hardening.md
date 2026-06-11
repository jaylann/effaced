# Hardening the audit trail: append-only at the database

`effaced_audit_events` is append-only **by construction** in the library:
no code path updates or deletes rows, and the `AuditSink` protocol exposes
no surface to do so. The table's schema alone, however, cannot stop raw SQL
or another application from rewriting history.

If your threat model includes writers outside effaced, add a Postgres
trigger that rejects mutation at the database itself. This is optional
hardening of the mechanism — not a compliance determination.

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
