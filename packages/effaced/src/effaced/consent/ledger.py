"""The :class:`ConsentLedger` — record, withdraw, and query consent."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.consent.record import ConsentRecord

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.orm import Session

    from effaced.audit.sink import AuditSink


class ConsentLedger:
    """Append-only consent bookkeeping (Art. 7).

    Withdrawal is as easy as granting — both are one call appending one
    immutable record. Every call is mirrored into the audit trail.

    Consent rows are written through the caller's session and commit with
    it; the mirrored audit event goes through the constructor's sink, which
    persists independently (ADR 0006). A failing sink raises out of
    :meth:`record` before the caller can commit, so no consent change can
    persist unaudited — the converse (an audit event for a consent write
    the caller then rolls back) is possible and is the deliberate, evidence-
    preserving direction. Do not commit a session after :meth:`record`
    raised.
    """

    def __init__(self, consent_records: Table, audit_sink: AuditSink) -> None:
        """Wire the ledger to the consent table and the audit trail.

        Args:
            consent_records: The ``effaced_consent_records`` table handle
                from :func:`effaced.bind_tables`.
            audit_sink: Receives one event per recorded grant/withdrawal.
        """
        self._consent_records = consent_records
        self._audit_sink = audit_sink

    def record(self, session: Session, record: ConsentRecord) -> None:
        """Append one consent event (grant or withdrawal).

        Mirrors a ``CONSENT_GRANTED``/``CONSENT_WITHDRAWN`` audit event
        whose payload carries only the purpose and policy version — never
        the record's ``source`` or any other potential PII.

        Args:
            session: An open database session.
            record: The event to append. Never updates existing records.
        """
        session.execute(self._consent_records.insert().values(**record.model_dump()))
        event_type = (
            AuditEventType.CONSENT_GRANTED if record.granted else AuditEventType.CONSENT_WITHDRAWN
        )
        self._audit_sink.append(
            AuditEvent(
                event_id=uuid4(),
                event_type=event_type,
                subject_ref=record.subject_id,
                occurred_at=record.recorded_at,
                payload={"purpose": record.purpose, "policy_version": record.policy_version},
            )
        )

    def status(self, session: Session, subject_id: str, purpose: str) -> bool:
        """Whether the subject currently consents to a purpose.

        Derived from the latest record for (subject, purpose); ``False``
        when no record exists. "Latest" means greatest ``recorded_at``
        (supply distinct, caller-clock timestamps). Exact timestamp ties
        resolve to the withdrawing record — when the order of a grant and
        a withdrawal is unknowable, effaced assumes consent was withdrawn.

        Args:
            session: An open database session.
            subject_id: Whose consent to check.
            purpose: The processing purpose.

        Returns:
            Current consent status.
        """
        columns = self._consent_records.c
        statement = (
            self._consent_records.select()
            .where(columns.subject_id == subject_id, columns.purpose == purpose)
            .order_by(columns.recorded_at.desc(), columns.granted.asc(), columns.record_id.desc())
            .limit(1)
        )
        row = session.execute(statement).mappings().first()
        return False if row is None else bool(row["granted"])

    def history(self, session: Session, subject_id: str) -> tuple[ConsentRecord, ...]:
        """Every consent event for one subject, oldest first.

        Args:
            session: An open database session.
            subject_id: Whose history to read.

        Returns:
            The full, unredacted event sequence — this is the Art. 5(2)
            accountability answer.
        """
        columns = self._consent_records.c
        statement = (
            self._consent_records.select()
            .where(columns.subject_id == subject_id)
            .order_by(columns.recorded_at.asc(), columns.record_id.asc())
        )
        rows = session.execute(statement).mappings()
        return tuple(
            ConsentRecord(
                subject_id=row["subject_id"],
                purpose=row["purpose"],
                policy_version=row["policy_version"],
                granted=row["granted"],
                recorded_at=row["recorded_at"],
                source=row["source"],
            )
            for row in rows
        )
