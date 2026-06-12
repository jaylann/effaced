"""The :class:`RestrictionLedger` — place, lift, and query restriction."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.restriction.record import RestrictionRecord

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.orm import Session

    from effaced.audit.sink import AuditSink


class RestrictionLedger:
    """Append-only restriction bookkeeping (Art. 18).

    Recital 67's first method — the restriction "clearly indicated in the
    system" — is exactly what this ledger ships: a queryable flag plus its
    audited history. Nothing enforces it; :meth:`status` is the surface
    your application consults before processing.

    Restriction rows are written through the caller's session and commit
    with it; the mirrored audit event goes through the constructor's sink,
    which persists independently (ADR 0006). A failing sink raises out of
    :meth:`record` before the caller can commit, so no restriction change
    can persist unaudited — the converse (an audit event for a write the
    caller then rolls back) is possible and is the deliberate, evidence-
    preserving direction. Do not commit a session after :meth:`record`
    raised.
    """

    def __init__(self, restriction_records: Table, audit_sink: AuditSink) -> None:
        """Wire the ledger to the restriction table and the audit trail.

        Args:
            restriction_records: The ``effaced_restriction_records`` table
                handle from :func:`effaced.bind_tables`.
            audit_sink: Receives one event per recorded placement/lift.
        """
        self._restriction_records = restriction_records
        self._audit_sink = audit_sink

    def record(self, session: Session, record: RestrictionRecord) -> None:
        """Append one restriction event (placement or lift).

        Mirrors a ``RESTRICTION_PLACED``/``RESTRICTION_LIFTED`` audit event
        whose payload carries only the scope — ``{"purpose": ...}`` for a
        purpose-scoped record, ``{"scope": "all"}`` for a global one. The
        record's ``reason`` and ``source`` never enter the payload: free
        text is PII-bearing by nature.

        Args:
            session: An open database session.
            record: The event to append. Never updates existing records.
        """
        session.execute(self._restriction_records.insert().values(**record.model_dump()))
        event_type = (
            AuditEventType.RESTRICTION_PLACED
            if record.restricted
            else AuditEventType.RESTRICTION_LIFTED
        )
        payload: dict[str, str | int | bool] = (
            {"purpose": record.purpose} if record.purpose is not None else {"scope": "all"}
        )
        self._audit_sink.append(
            AuditEvent(
                event_id=uuid4(),
                event_type=event_type,
                subject_ref=record.subject_id,
                occurred_at=record.recorded_at,
                payload=payload,
            )
        )

    def status(self, session: Session, subject_id: str, purpose: str | None = None) -> bool:
        """Whether the subject's processing is currently restricted.

        Derived, never stored. The answer considers two events: the latest
        global record (``purpose IS NULL``) and, when ``purpose`` is given,
        the latest record for that purpose — restricted if **either**
        restricts. A purpose-level lift therefore cannot undo a global
        restriction; lift globally instead. With ``purpose=None`` only the
        global records are consulted, so a purpose-scoped restriction does
        not answer the all-processing question. ``False`` when no record
        exists.

        "Latest" means greatest ``recorded_at`` (supply distinct,
        caller-clock timestamps). Exact timestamp ties resolve to the
        restricting record — when the order of a placement and a lift is
        unknowable, effaced assumes the subject is restricted, the same
        protective direction as consent's withdrawn-wins tie-break.

        Args:
            session: An open database session.
            subject_id: Whose restriction to check.
            purpose: The processing purpose, or ``None`` for the
                all-processing question.

        Returns:
            Current restriction status.
        """
        if self._latest_scope_restricted(session, subject_id, None):
            return True
        if purpose is None:
            return False
        return self._latest_scope_restricted(session, subject_id, purpose)

    def history(self, session: Session, subject_id: str) -> tuple[RestrictionRecord, ...]:
        """Every restriction event for one subject, oldest first.

        Equal ``recorded_at`` values order by ``record_id``. Records come
        back full and unredacted — ``reason`` and ``source`` included.
        Together with the ``RESTRICTION_LIFTED`` audit event this is the
        mechanical substrate for the Art. 18(3) duty to inform the subject
        before a restriction is lifted; the informing itself stays the
        controller's process.

        Args:
            session: An open database session.
            subject_id: Whose history to read.

        Returns:
            The full, unredacted event sequence.
        """
        columns = self._restriction_records.c
        statement = (
            self._restriction_records.select()
            .where(columns.subject_id == subject_id)
            .order_by(columns.recorded_at.asc(), columns.record_id.asc())
        )
        rows = session.execute(statement).mappings()
        return tuple(
            RestrictionRecord(
                subject_id=row["subject_id"],
                purpose=row["purpose"],
                restricted=row["restricted"],
                reason=row["reason"],
                recorded_at=row["recorded_at"],
                source=row["source"],
            )
            for row in rows
        )

    def _latest_scope_restricted(
        self, session: Session, subject_id: str, purpose: str | None
    ) -> bool:
        """The ``restricted`` flag of one scope's latest record; restricted wins ties."""
        columns = self._restriction_records.c
        scope = columns.purpose.is_(None) if purpose is None else columns.purpose == purpose
        statement = (
            self._restriction_records.select()
            .where(columns.subject_id == subject_id, scope)
            .order_by(
                columns.recorded_at.desc(), columns.restricted.desc(), columns.record_id.desc()
            )
            .limit(1)
        )
        row = session.execute(statement).mappings().first()
        return False if row is None else bool(row["restricted"])
