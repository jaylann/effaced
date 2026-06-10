"""Builder for the ``effaced_audit_events`` table."""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, Index, MetaData, String, Table, Uuid
from sqlalchemy.dialects.postgresql import JSONB

AUDIT_EVENTS_TABLE_NAME = "effaced_audit_events"


def build_audit_events_table(metadata: MetaData) -> Table:
    """Define the append-only audit trail table on ``metadata``.

    The table is insert-only by construction: the library exposes no update
    or delete path for it. The schema alone cannot forbid raw SQL writes —
    see ``docs/runbooks/append-only-audit-hardening.md`` for an optional
    Postgres trigger that rejects ``UPDATE``/``DELETE`` at the database.

    Type choices, shared by all effaced-owned tables: UUID primary keys
    (``Uuid`` — native on Postgres, ``CHAR(32)`` elsewhere), timezone-aware
    timestamps (``timestamptz`` on Postgres), and JSON payloads stored as
    ``JSONB`` on Postgres with a plain ``JSON`` fallback elsewhere.
    ``event_type`` is a plain string rather than a native database enum:
    the :class:`~effaced.AuditEventType` vocabulary grows in MINOR releases,
    and a native enum would force a schema migration on every addition.

    Args:
        metadata: The application's ``MetaData`` to mount the table on.

    Returns:
        The mounted ``Table``.
    """
    return Table(
        AUDIT_EVENTS_TABLE_NAME,
        metadata,
        Column("event_id", Uuid(), primary_key=True),
        Column("event_type", String(64), nullable=False),
        Column("subject_ref", String(255), nullable=False),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict),
        Index(
            "ix_effaced_audit_events_subject_ref_occurred_at",
            "subject_ref",
            "occurred_at",
        ),
    )
