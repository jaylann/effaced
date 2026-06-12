"""Translation between :class:`OutboxEntry`, table rows, and audit events.

Private to the :class:`~effaced.Outbox`: the row⇄entry mappers (:func:`_row`
is the forward direction, :func:`_entry`/:func:`_claimed`/:func:`_requeued_entry`
the reverse, each a deliberate mirror), plus the requeue's append-first event
builder and the rectify-entry guard. Kept out of ``outbox.py`` so the class file
stays one concept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.annotations import Correction, SubjectRef
from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.exceptions import ConfigurationError
from effaced.saga.outbox_entry import OutboxEntry
from effaced.saga.outbox_operation import OutboxOperation
from effaced.saga.outbox_status import OutboxStatus

if TYPE_CHECKING:
    from datetime import timedelta

    from sqlalchemy import RowMapping


def _row(entry: OutboxEntry) -> dict[str, object]:
    """Flatten one entry into the outbox table's column values."""
    return {
        "entry_id": entry.entry_id,
        "subject_id": entry.subject_id,
        "resolver": entry.resolver,
        "ref_kind": entry.ref.kind,
        "ref_value": entry.ref.value,
        "ref_extra": dict(entry.ref.extra),
        "operation": entry.operation.value,
        "payload": (
            {"corrections": [c.model_dump(mode="json") for c in entry.corrections]}
            if entry.corrections
            else None
        ),
        "status": entry.status.value,
        "attempts": entry.attempts,
        "enqueued_at": entry.enqueued_at,
        "last_attempt_at": entry.last_attempt_at,
        "next_attempt_at": entry.next_attempt_at,
        "last_error": entry.last_error,
    }


def _corrections(payload: object) -> tuple[Correction, ...]:
    """Reconstruct a row's corrections; an absent or empty payload is ``()``."""
    if not isinstance(payload, dict):
        return ()
    return tuple(Correction.model_validate(item) for item in payload.get("corrections", ()))


def _claimed(row: RowMapping, *, now: datetime, lease: timedelta) -> OutboxEntry:
    """One claimed entry in its post-claim state (mirror of :func:`_row`)."""
    return OutboxEntry(
        entry_id=row["entry_id"],
        subject_id=row["subject_id"],
        resolver=row["resolver"],
        ref=SubjectRef(kind=row["ref_kind"], value=row["ref_value"], extra=row["ref_extra"]),
        operation=OutboxOperation(row["operation"]),
        corrections=_corrections(row["payload"]),
        status=OutboxStatus.IN_FLIGHT,
        attempts=row["attempts"] + 1,
        enqueued_at=row["enqueued_at"],
        last_attempt_at=now,
        next_attempt_at=now + lease,
        last_error=row["last_error"],
    )


def _entry(row: RowMapping) -> OutboxEntry:
    """One entry exactly as stored (mirror of :func:`_row`)."""
    return OutboxEntry(
        entry_id=row["entry_id"],
        subject_id=row["subject_id"],
        resolver=row["resolver"],
        ref=SubjectRef(kind=row["ref_kind"], value=row["ref_value"], extra=row["ref_extra"]),
        operation=OutboxOperation(row["operation"]),
        corrections=_corrections(row["payload"]),
        status=OutboxStatus(row["status"]),
        attempts=row["attempts"],
        enqueued_at=row["enqueued_at"],
        last_attempt_at=row["last_attempt_at"],
        next_attempt_at=row["next_attempt_at"],
        last_error=row["last_error"],
    )


def _reject_rectify_entries(rows: list[RowMapping]) -> None:
    """Refuse to requeue an abandoned rectify entry (ADR 0013 and ADR 0015).

    A rectify entry's corrections are cleared at abandonment, so requeuing
    it would re-execute with nothing to apply — a silent no-op rectification
    that still completes. Raised before any append or flip; re-issue the
    rectification via the :class:`~effaced.Rectifier` instead.
    """
    rectify = [row for row in rows if OutboxOperation(row["operation"]) is OutboxOperation.RECTIFY]
    if not rectify:
        return
    ids = ", ".join(str(row["entry_id"]) for row in rectify)
    msg = (
        f"cannot requeue abandoned rectify entries ({ids}): their corrections were "
        "cleared at abandonment (ADR 0013); re-issue the rectification via the Rectifier"
    )
    raise ConfigurationError(msg)


def _requeued_event(row: RowMapping) -> AuditEvent:
    """The append-first ``ERASURE_REQUEUED`` event for one abandoned row.

    Requeue is erase-only (rectify entries are refused upstream), so the
    event type is fixed. The payload carries the prior struggle
    (``prior_attempts``/``prior_error`` — the exception *class name* only)
    so the row's columns can reset to a fresh budget. ``prior_error`` is
    omitted entirely when the row carried none, rather than emitted as an
    empty string — an abandoned row always has one, so this is defensive,
    but the payload shape is MAJOR-protected and an absent error is "no
    error", never the empty-string error class.
    """
    payload: dict[str, str | int | bool] = {
        "entry_id": str(row["entry_id"]),
        "resolver": row["resolver"],
        "prior_attempts": row["attempts"],
    }
    if row["last_error"] is not None:
        payload["prior_error"] = row["last_error"]
    return AuditEvent(
        event_id=uuid4(),
        event_type=AuditEventType.ERASURE_REQUEUED,
        subject_ref=row["subject_id"],
        occurred_at=datetime.now(UTC),
        payload=payload,
    )


def _requeued_entry(row: RowMapping) -> OutboxEntry:
    """One entry in its post-requeue ``PENDING`` state (fresh retry budget)."""
    return _entry(row).model_copy(
        update={
            "status": OutboxStatus.PENDING,
            "attempts": 0,
            "next_attempt_at": None,
            "last_error": None,
        }
    )
