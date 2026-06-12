"""Builder for the ``effaced_restriction_records`` table."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, MetaData, String, Table, Uuid

RESTRICTION_RECORDS_TABLE_NAME = "effaced_restriction_records"


def build_restriction_records_table(metadata: MetaData) -> Table:
    """Define the restriction event table on ``metadata``.

    Rows are immutable events mirroring :class:`~effaced.RestrictionRecord`;
    current restriction status is *derived* by reading the latest record per
    scope (global ``purpose IS NULL`` plus the queried purpose), never
    stored. There is therefore no unique constraint — the same scope
    legitimately repeats with every placement and lift. The composite index
    serves exactly that latest-per-scope read. ``record_id`` is a surrogate
    key (the domain model carries no id); it defaults to a client-side
    ``uuid4`` so no database extension or server default is required.

    Args:
        metadata: The application's ``MetaData`` to mount the table on.

    Returns:
        The mounted ``Table``.
    """
    return Table(
        RESTRICTION_RECORDS_TABLE_NAME,
        metadata,
        Column("record_id", Uuid(), primary_key=True, default=uuid4),
        Column("subject_id", String(255), nullable=False),
        Column("purpose", String(255), nullable=True),
        Column("restricted", Boolean(), nullable=False),
        Column("reason", String(255), nullable=True),
        Column("recorded_at", DateTime(timezone=True), nullable=False),
        Column("source", String(255), nullable=True),
        Index(
            "ix_effaced_restriction_records_subject_purpose_recorded_at",
            "subject_id",
            "purpose",
            "recorded_at",
        ),
    )
