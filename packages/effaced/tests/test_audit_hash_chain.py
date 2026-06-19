"""Pure-function tests for compute_event_hash (ADR 0028).

Proves: determinism, order/content sensitivity (any field change breaks the
digest), digest shape, chaining vs non-chaining, canonical key ordering, and
occurred_at instant-invariance (aware UTC, naive UTC, and a non-UTC offset of
the same instant all hash identically; a different instant differs).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from effaced import AuditEvent, AuditEventType, compute_event_hash


def _event_at(occurred_at: datetime) -> AuditEvent:
    """A fixed event differing only in occurred_at — for instant-invariance."""
    return AuditEvent(
        event_id=_BASE_EVENT.event_id,
        event_type=_BASE_EVENT.event_type,
        subject_ref=_BASE_EVENT.subject_ref,
        occurred_at=occurred_at,
        payload=dict(_BASE_EVENT.payload),
    )


_BASE_EVENT = AuditEvent(
    event_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    event_type=AuditEventType.CONSENT_GRANTED,
    subject_ref="subject-1",
    occurred_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    payload={"purpose": "analytics"},
)


def test_digest_is_64_lowercase_hex_chars() -> None:
    digest = compute_event_hash(_BASE_EVENT, None)
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_same_inputs_produce_identical_digest() -> None:
    d1 = compute_event_hash(_BASE_EVENT, "abc123")
    d2 = compute_event_hash(_BASE_EVENT, "abc123")
    assert d1 == d2


def test_chaining_differs_from_non_chaining() -> None:
    without_prior = compute_event_hash(_BASE_EVENT, None)
    with_prior = compute_event_hash(_BASE_EVENT, "somehash")
    assert without_prior != with_prior


def test_changing_prior_hash_changes_digest() -> None:
    d1 = compute_event_hash(_BASE_EVENT, "aaaa")
    d2 = compute_event_hash(_BASE_EVENT, "bbbb")
    assert d1 != d2


def test_changing_event_id_changes_digest() -> None:
    other = AuditEvent(
        event_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        event_type=_BASE_EVENT.event_type,
        subject_ref=_BASE_EVENT.subject_ref,
        occurred_at=_BASE_EVENT.occurred_at,
        payload=dict(_BASE_EVENT.payload),
    )
    assert compute_event_hash(_BASE_EVENT, None) != compute_event_hash(other, None)


def test_changing_event_type_changes_digest() -> None:
    other = AuditEvent(
        event_id=_BASE_EVENT.event_id,
        event_type=AuditEventType.CONSENT_WITHDRAWN,
        subject_ref=_BASE_EVENT.subject_ref,
        occurred_at=_BASE_EVENT.occurred_at,
        payload=dict(_BASE_EVENT.payload),
    )
    assert compute_event_hash(_BASE_EVENT, None) != compute_event_hash(other, None)


def test_changing_subject_ref_changes_digest() -> None:
    other = AuditEvent(
        event_id=_BASE_EVENT.event_id,
        event_type=_BASE_EVENT.event_type,
        subject_ref="subject-2",
        occurred_at=_BASE_EVENT.occurred_at,
        payload=dict(_BASE_EVENT.payload),
    )
    assert compute_event_hash(_BASE_EVENT, None) != compute_event_hash(other, None)


def test_changing_occurred_at_changes_digest() -> None:
    other = AuditEvent(
        event_id=_BASE_EVENT.event_id,
        event_type=_BASE_EVENT.event_type,
        subject_ref=_BASE_EVENT.subject_ref,
        occurred_at=datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC),
        payload=dict(_BASE_EVENT.payload),
    )
    assert compute_event_hash(_BASE_EVENT, None) != compute_event_hash(other, None)


def test_changing_payload_changes_digest() -> None:
    other = AuditEvent(
        event_id=_BASE_EVENT.event_id,
        event_type=_BASE_EVENT.event_type,
        subject_ref=_BASE_EVENT.subject_ref,
        occurred_at=_BASE_EVENT.occurred_at,
        payload={"purpose": "marketing"},
    )
    assert compute_event_hash(_BASE_EVENT, None) != compute_event_hash(other, None)


def test_payload_key_order_does_not_change_digest() -> None:
    """sort_keys canonicalization: insertion order of payload keys is irrelevant."""
    event_alpha_first = AuditEvent(
        event_id=_BASE_EVENT.event_id,
        event_type=_BASE_EVENT.event_type,
        subject_ref=_BASE_EVENT.subject_ref,
        occurred_at=_BASE_EVENT.occurred_at,
        payload={"alpha": "1", "beta": "2"},
    )
    event_beta_first = AuditEvent(
        event_id=_BASE_EVENT.event_id,
        event_type=_BASE_EVENT.event_type,
        subject_ref=_BASE_EVENT.subject_ref,
        occurred_at=_BASE_EVENT.occurred_at,
        payload={"beta": "2", "alpha": "1"},
    )
    assert compute_event_hash(event_alpha_first, None) == compute_event_hash(event_beta_first, None)


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("event_id", uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")),
        ("event_type", AuditEventType.ERASURE_REQUESTED),
        ("subject_ref", "changed"),
        ("occurred_at", datetime(2099, 12, 31, tzinfo=UTC)),
        ("payload", {"changed": True}),
    ],
)
def test_any_field_change_breaks_digest(field: str, replacement: object) -> None:
    kwargs = {
        "event_id": _BASE_EVENT.event_id,
        "event_type": _BASE_EVENT.event_type,
        "subject_ref": _BASE_EVENT.subject_ref,
        "occurred_at": _BASE_EVENT.occurred_at,
        "payload": dict(_BASE_EVENT.payload),
    }
    kwargs[field] = replacement
    other = AuditEvent(**kwargs)  # type: ignore[arg-type]
    assert compute_event_hash(_BASE_EVENT, None) != compute_event_hash(other, None)


def test_occurred_at_hash_is_invariant_to_timezone_representation() -> None:
    """The same UTC instant hashes identically however it is expressed.

    Pins _canonical_occurred_at: occurred_at is normalized to the UTC instant
    and emitted offset-free, so aware-UTC, the equivalent naive value (the
    trail's UTC-by-contract), and the same instant in a non-UTC offset all
    collapse to one digest. Kills a mutant that would isoformat() the raw
    value (which would diverge by a ``+00:00`` / ``+02:00`` suffix and make a
    timestamptz read-back look tampered — exactly the SQLite tzinfo-drop bug).
    """
    instant = datetime(2026, 1, 1, 12, 0, 0)
    aware_utc = _event_at(instant.replace(tzinfo=UTC))
    naive_utc = _event_at(instant)
    # Same instant as 14:00 at +02:00 (== 12:00 UTC).
    plus_two = _event_at(datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))))

    digest = compute_event_hash(aware_utc, None)
    assert compute_event_hash(naive_utc, None) == digest
    assert compute_event_hash(plus_two, None) == digest


def test_occurred_at_hash_differs_for_a_different_instant() -> None:
    """A genuinely different instant still changes the digest."""
    instant = _event_at(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))
    one_hour_later = _event_at(datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC))
    assert compute_event_hash(instant, None) != compute_event_hash(one_hour_later, None)
