"""The :class:`ConsentLedger` — record, withdraw, and query consent."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from effaced.consent.record import ConsentRecord


class ConsentLedger:
    """Append-only consent bookkeeping (Art. 7).

    Withdrawal is as easy as granting — both are one call appending one
    immutable record. Every call is mirrored into the audit trail.
    """

    async def record(self, session: Session, record: ConsentRecord) -> None:
        """Append one consent event (grant or withdrawal).

        Args:
            session: An open database session.
            record: The event to append. Never updates existing records.
        """
        raise NotImplementedError

    async def status(self, session: Session, subject_id: str, purpose: str) -> bool:
        """Whether the subject currently consents to a purpose.

        Derived from the latest record for (subject, purpose); ``False``
        when no record exists.

        Args:
            session: An open database session.
            subject_id: Whose consent to check.
            purpose: The processing purpose.

        Returns:
            Current consent status.
        """
        raise NotImplementedError

    async def history(self, session: Session, subject_id: str) -> tuple[ConsentRecord, ...]:
        """Every consent event for one subject, oldest first.

        Args:
            session: An open database session.
            subject_id: Whose history to read.

        Returns:
            The full, unredacted event sequence — this is the Art. 5(2)
            accountability answer.
        """
        raise NotImplementedError
