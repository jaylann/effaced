"""Builder for the ``effaced_consent_records`` table."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, MetaData, String, Table, Uuid

CONSENT_RECORDS_TABLE_NAME = "effaced_consent_records"


def build_consent_records_table(metadata: MetaData) -> Table:
    """Define the consent event table on ``metadata``.

    Rows are immutable events mirroring :class:`~effaced.ConsentRecord`;
    current consent status is *derived* by reading the latest record per
    ``(subject_id, purpose)``, never stored. There is therefore no unique
    constraint — the same subject/purpose pair legitimately repeats with
    every grant and withdrawal. The composite index serves exactly that
    latest-per-pair read. ``record_id`` is a surrogate key (the domain model
    carries no id); it defaults to a client-side ``uuid4`` so no database
    extension or server default is required.

    Args:
        metadata: The application's ``MetaData`` to mount the table on.

    Returns:
        The mounted ``Table``.
    """
    return Table(
        CONSENT_RECORDS_TABLE_NAME,
        metadata,
        Column("record_id", Uuid(), primary_key=True, default=uuid4),
        Column("subject_id", String(255), nullable=False),
        Column("purpose", String(255), nullable=False),
        Column("policy_version", String(255), nullable=False),
        Column("granted", Boolean(), nullable=False),
        Column("recorded_at", DateTime(timezone=True), nullable=False),
        Column("source", String(255), nullable=True),
        Index(
            "ix_effaced_consent_records_subject_purpose_recorded_at",
            "subject_id",
            "purpose",
            "recorded_at",
        ),
    )
