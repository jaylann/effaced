"""Builder for the ``effaced_subject_erasures`` tombstone table."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, String, Table

SUBJECT_ERASURES_TABLE_NAME = "effaced_subject_erasures"

SUBJECT_ERASURE_REQUESTED = "requested"
"""``status`` while a subject's local erasure is in flight (tombstone upserted,
not yet marked complete)."""

SUBJECT_ERASURE_COMPLETED = "completed"
"""``status`` once a subject's local erasure has committed (``erased_at`` set)."""


def build_subject_erasures_table(metadata: MetaData) -> Table:
    """Define the subject-erasure tombstone table on ``metadata`` (ADR 0026).

    One row per subject that has had an erasure requested, keyed by the
    canonical ``subject_ref`` — the same scalar the audit trail and outbox
    store, so a composite subject key and its single-column equivalent
    tombstone identically (ADR 0025). The row is the **serialization point**
    for concurrent erasures of one subject: :meth:`~effaced.SubjectLock.
    acquire` upserts it and takes ``SELECT … FOR UPDATE`` on it, so a second
    same-subject erasure blocks until the first commits. It is also the
    **detection surface** a controller can consult before re-creating a
    subject — effaced serializes and locks, it does not determine that
    re-creation is prevented.

    Type choices mirror the other effaced-owned tables: timezone-aware
    timestamps (``timestamptz`` on Postgres) and a plain-string ``status``
    rather than a native database enum, so the lifecycle vocabulary can grow
    in MINOR releases without forcing a schema migration. The table is
    additive by construction (ADR 0021); ``metadata.create_all`` creates it
    and the caller's next ``alembic revision --autogenerate`` proposes a
    single ``add_table``.

    Args:
        metadata: The application's ``MetaData`` to mount the table on.

    Returns:
        The mounted ``Table``.
    """
    return Table(
        SUBJECT_ERASURES_TABLE_NAME,
        metadata,
        Column("subject_ref", String(255), primary_key=True),
        Column("requested_at", DateTime(timezone=True), nullable=False),
        Column("erased_at", DateTime(timezone=True), nullable=True),
        Column("status", String(32), nullable=False),
    )
