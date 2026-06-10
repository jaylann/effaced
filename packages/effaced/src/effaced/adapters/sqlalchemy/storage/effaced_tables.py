"""The :class:`EffacedTables` container of mounted table handles."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Table


@dataclass(frozen=True, slots=True)
class EffacedTables:
    """Handles to the three effaced-owned tables mounted on a ``MetaData``.

    Returned by :func:`effaced.bind_tables` so downstream components (audit
    sink, consent ledger, outbox) can reference the tables directly instead
    of looking them up by name.

    Attributes:
        audit_events: The append-only audit trail table.
        consent_records: The append-only consent event table.
        outbox: The durable external-call outbox table.
    """

    audit_events: Table
    consent_records: Table
    outbox: Table
