"""The :func:`compute_event_hash` chaining primitive (ADR 0028)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from effaced.audit.event import AuditEvent

# Fixed, offset-free wall-clock format (always microseconds), applied after
# normalizing the instant to UTC. See _canonical_occurred_at.
_OCCURRED_AT_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"


def _canonical_occurred_at(occurred_at: datetime) -> str:
    """Encode ``occurred_at`` as a driver-stable UTC wall-clock string.

    The trail's timestamps are UTC by contract (:class:`~effaced.AuditEvent`),
    but a ``timestamptz`` column round-trips them back **aware** on some
    drivers (psycopg) and **naive** on others (SQLite drops ``tzinfo``). A
    raw ``isoformat()`` would then differ by a ``+00:00`` suffix between the
    value hashed at append time and the value read back at verify time —
    making every row look tampered. Normalizing to the UTC instant and
    emitting a fixed offset-free format makes the digest identical regardless
    of how the timestamp survived storage.

    An aware value is converted to UTC; a naive value is taken to already be
    UTC (the trail's contract), so both collapse to the same encoding.
    """
    if occurred_at.tzinfo is not None and occurred_at.utcoffset() is not None:
        occurred_at = occurred_at.astimezone(UTC)
    return occurred_at.replace(tzinfo=None).strftime(_OCCURRED_AT_FORMAT)


def compute_event_hash(event: AuditEvent, prior_hash: str | None) -> str:
    """Hash one event chained to its predecessor's hash (ADR 0028).

    The tamper-evidence primitive: an event's hash binds its own load-bearing
    content to the prior event's hash, so editing any field of any recorded
    row — or reordering rows — changes its recomputed hash and breaks every
    later link. Recomputing the whole chain therefore *detects* an
    out-of-band modification and localizes the first break. It makes such a
    modification **detectable, not impossible** — a writer with table access
    can recompute the chain forward.

    The encoding is canonical, deterministic, and order-sensitive, and is
    frozen as audit behaviour (ADR 0028 / widened SemVer): the event's
    ``event_id``, ``event_type``, ``subject_ref``, ``occurred_at``, and
    ``payload`` are serialized with the ``prior_hash`` into a single JSON
    object with sorted keys and the tightest separators, then SHA-256 hex
    digested. ``event_id`` is encoded as its canonical UUID string and
    ``occurred_at`` is normalized to a UTC, offset-free wall-clock string
    (see :func:`_canonical_occurred_at`) so the digest is stable however a
    ``timestamptz`` round-trips it — aware on psycopg, naive on SQLite — and
    the value hashed at append time matches the value verified after a
    read-back. ``payload`` is already restricted to short scalars
    (:class:`~effaced.AuditEvent`), for which JSON and python serialization
    coincide.

    Args:
        event: The event to hash. Never mutated; no field is read that the
            caller-facing model does not already expose.
        prior_hash: The ``event_hash`` of the immediately preceding chained
            event, or ``None`` for the first chained event in a trail (or
            when the predecessor is an unchained legacy row).

    Returns:
        The lowercase hex SHA-256 digest — 64 characters, matching the
        column width.
    """
    canonical = json.dumps(
        {
            "event_id": str(event.event_id),
            "event_type": event.event_type.value,
            "subject_ref": event.subject_ref,
            "occurred_at": _canonical_occurred_at(event.occurred_at),
            "payload": event.payload,
            "prior_hash": prior_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
