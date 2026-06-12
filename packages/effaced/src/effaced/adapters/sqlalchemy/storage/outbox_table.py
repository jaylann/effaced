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

from effaced.saga.outbox_operation import OutboxOperation
from effaced.saga.outbox_status import OutboxStatus

OUTBOX_TABLE_NAME = "effaced_outbox"


def build_outbox_table(metadata: MetaData) -> Table:
    """Define the durable external-call outbox table on ``metadata``.

    Rows mirror :class:`~effaced.OutboxEntry`. The nested
    :class:`~effaced.SubjectRef` is flattened into queryable ``ref_kind`` /
    ``ref_value`` columns plus a ``ref_extra`` JSON column for the free-form
    string mapping. ``status`` and ``operation`` are plain strings rather
    than native database enums so :class:`~effaced.OutboxStatus` and
    :class:`~effaced.OutboxOperation` can grow in MINOR releases without
    forcing user migrations; ``operation`` additionally carries a server
    default so the additive ``ALTER TABLE`` works on populated outboxes.
    ``payload`` holds a rectify entry's corrections — real PII, nullable,
    cleared the moment the entry reaches a terminal status. The
    ``(status, enqueued_at)`` index serves the runner's claim of due
    entries, oldest first; ``next_attempt_at`` is the claim gate (``NULL``
    = due now, a crash lease while in flight, the backoff schedule while
    failed); the ``subject_id`` index serves the runner's
    per-(subject, operation) completion check.

    Args:
        metadata: The application's ``MetaData`` to mount the table on.

    Returns:
        The mounted ``Table``.
    """
    return Table(
        OUTBOX_TABLE_NAME,
        metadata,
        Column("entry_id", Uuid(), primary_key=True),
        Column("subject_id", String(255), nullable=False),
        Column("resolver", String(255), nullable=False),
        Column("ref_kind", String(255), nullable=False),
        Column("ref_value", String(255), nullable=False),
        Column(
            "ref_extra", JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
        ),
        Column(
            "operation",
            String(32),
            nullable=False,
            default=OutboxOperation.ERASE.value,
            server_default=OutboxOperation.ERASE.value,
        ),
        Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=True),
        Column("status", String(32), nullable=False, default=OutboxStatus.PENDING.value),
        Column("attempts", Integer(), nullable=False, default=0),
        Column("enqueued_at", DateTime(timezone=True), nullable=False),
        Column("last_attempt_at", DateTime(timezone=True), nullable=True),
        Column("next_attempt_at", DateTime(timezone=True), nullable=True),
        Column("last_error", Text(), nullable=True),
        Index("ix_effaced_outbox_status_enqueued_at", "status", "enqueued_at"),
        Index("ix_effaced_outbox_subject_id", "subject_id"),
    )
