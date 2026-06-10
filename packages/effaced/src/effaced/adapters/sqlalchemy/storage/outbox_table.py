"""Builder for the ``effaced_outbox`` table."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from effaced.saga.outbox_status import OutboxStatus

OUTBOX_TABLE_NAME = "effaced_outbox"


def build_outbox_table(metadata: MetaData) -> Table:
    """Define the durable external-call outbox table on ``metadata``.

    Rows mirror :class:`~effaced.OutboxEntry`. The nested
    :class:`~effaced.SubjectRef` is flattened into queryable ``ref_kind`` /
    ``ref_value`` columns plus a ``ref_extra`` JSON column for the free-form
    string mapping. ``status`` is a plain string rather than a native
    database enum so :class:`~effaced.OutboxStatus` can grow in MINOR
    releases without forcing user migrations. The ``(status, enqueued_at)``
    index serves the runner's claim of pending entries, oldest first.

    Args:
        metadata: The application's ``MetaData`` to mount the table on.

    Returns:
        The mounted ``Table``.
    """
    return Table(
        OUTBOX_TABLE_NAME,
        metadata,
        Column("entry_id", Uuid(), primary_key=True),
        Column("resolver", String(255), nullable=False),
        Column("ref_kind", String(255), nullable=False),
        Column("ref_value", String(255), nullable=False),
        Column(
            "ref_extra", JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
        ),
        Column("status", String(32), nullable=False, default=OutboxStatus.PENDING.value),
        Column("attempts", Integer(), nullable=False, default=0),
        Column("enqueued_at", DateTime(timezone=True), nullable=False),
        Column("last_attempt_at", DateTime(timezone=True), nullable=True),
        Column("last_error", Text(), nullable=True),
        Index("ix_effaced_outbox_status_enqueued_at", "status", "enqueued_at"),
    )
