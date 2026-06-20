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

    ``prior_hash`` and ``event_hash`` carry an optional tamper-evidence hash
    chain (ADR 0028): each chained row hashes its own content together with
    the prior row's hash, so an out-of-band edit of any recorded row is
    *detectable* by recomputing the chain — it is not *prevented*. Both
    columns are nullable: legacy rows and custom sinks that do not compute
    the chain leave them ``NULL`` and verify as an unchained prefix, never a
    failure. See :class:`~effaced.AuditChainVerifier`.

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
        Column("prior_hash", String(64), nullable=True),
        Column("event_hash", String(64), nullable=True),
        Index(
            "ix_effaced_audit_events_subject_ref_occurred_at",
            "subject_ref",
            "occurred_at",
        ),
    )
