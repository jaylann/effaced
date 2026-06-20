"""Property tests for the audit hash chain (ADR 0028).

The chain is a pure INSERTION-ORDER linked list: each appended row chains to
the current tail (``prior_hash = tail.event_hash``), and the verifier walks
the list from genesis (``prior_hash IS NULL``) following predecessor pointers.
``occurred_at`` plays no part in ordering the chain — it is caller-supplied and
backdatable through the consent/restriction ledgers — so these properties draw
arbitrary, even non-monotonic, timestamps and still expect a clean verify.

Properties:
  (a) UNTOUCHED: any sequence appended through the sink always verifies.
  (b) SINGLE-FIELD MUTATION: mutating one stored field of one row makes that
      row's recomputed hash mismatch, so verify() names it as the break.
  (c) NON-MONOTONIC TIMESTAMPS: backdated / clock-skewed appends still verify
      (regression for the reviewer's false-alarm bug).
  (d) NON-MONOTONIC + MUTATION: a content tamper is localized to the mutated
      row regardless of occurred_at order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditChainVerifier,
    AuditEvent,
    AuditEventType,
    DatabaseAuditSink,
    EffacedTables,
    bind_tables,
)

pytestmark = pytest.mark.property

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_EVENT_TYPES = list(AuditEventType)

# Digits are added via whitelist_characters rather than the "N" + "d" decimal
# category code: the typos pre-commit hook rewrites that bare two-letter token
# and silently corrupts the strategy (see git-workflow.md).
_subject_refs = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll"),
        whitelist_characters="-_0123456789",
    ),
    min_size=1,
    max_size=40,
)

_payloads = st.dictionaries(
    keys=st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll")),
        min_size=1,
        max_size=10,
    ),
    values=st.one_of(
        st.text(min_size=0, max_size=20),
        st.integers(min_value=0, max_value=9999),
        st.booleans(),
    ),
    max_size=3,
)

_BASE_DT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

# Per-event offsets that may run backwards, repeat (ties), or jump — so the
# insertion order and the occurred_at order diverge arbitrarily. The chain must
# verify regardless, because it is insertion-keyed, not time-keyed.
_offset_seconds = st.integers(min_value=-86_400, max_value=86_400)


def _make_events(
    subject_refs: list[str],
    payloads: list[dict[str, str | int | bool]],
    offsets: list[int],
) -> list[AuditEvent]:
    """Build events whose occurred_at is arbitrary relative to insertion order.

    occurred_at is irrelevant to the chain: the sink chains each event to the
    current tail (insertion order), and the verifier walks that linked list.
    The timestamps here may go backwards or tie between adjacent inserts; the
    chain must still verify. event_ids are unique per event.
    """
    n = len(subject_refs)
    return [
        AuditEvent(
            event_id=uuid4(),
            event_type=_EVENT_TYPES[i % len(_EVENT_TYPES)],
            subject_ref=subject_refs[i],
            occurred_at=_BASE_DT + timedelta(seconds=offsets[i]),
            payload=payloads[i],
        )
        for i in range(n)
    ]


_event_lists = (
    st.integers(min_value=1, max_value=8)
    .flatmap(
        lambda n: st.tuples(
            st.lists(_subject_refs, min_size=n, max_size=n),
            st.lists(_payloads, min_size=n, max_size=n),
            st.lists(_offset_seconds, min_size=n, max_size=n),
        )
    )
    .map(lambda t: _make_events(t[0], t[1], t[2]))
)


# ---------------------------------------------------------------------------
# Harness fixture
# ---------------------------------------------------------------------------


class ChainHarness(NamedTuple):
    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: DatabaseAuditSink
    verifier: AuditChainVerifier


def _make_harness() -> ChainHarness:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    factory = sessionmaker(engine)
    return ChainHarness(
        session_factory=factory,
        tables=tables,
        sink=DatabaseAuditSink(factory, tables.audit_events),
        verifier=AuditChainVerifier(factory, tables.audit_events),
    )


def _chained_event_ids(h: ChainHarness) -> list[object]:
    """Every chained row's event_id (no ordering assumption — insertion list)."""
    cols = h.tables.audit_events.c
    stmt = h.tables.audit_events.select().where(cols.event_hash.isnot(None))
    with h.session_factory() as session:
        return [row["event_id"] for row in session.execute(stmt).mappings().all()]


# ---------------------------------------------------------------------------
# Property (a): untouched chain always verifies
# ---------------------------------------------------------------------------


@given(events=_event_lists)
@settings(deadline=None)
def test_untouched_chain_always_verifies(events: list[AuditEvent]) -> None:
    """Any sequence appended through the sink verifies clean.

    Timestamps are arbitrary (the strategy draws non-monotonic offsets); the
    chain follows insertion order, so occurred_at never affects the verdict.
    """
    h = _make_harness()
    for event in events:
        h.sink.append(event)
    result = h.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


# ---------------------------------------------------------------------------
# Property (b): single-field mutation is always detected at the mutated row
# ---------------------------------------------------------------------------


@given(events=_event_lists, data=st.data())
@settings(deadline=None)
def test_single_field_mutation_is_detected_at_mutated_row(
    events: list[AuditEvent], data: st.DataObject
) -> None:
    """Mutating one field of one stored row names that row as the break.

    The chain is insertion-keyed, so occurred_at is irrelevant: the verifier
    walks genesis→tail along prior_hash pointers, reaches the mutated row, and
    its recomputed hash no longer matches its stored event_hash — so the
    mutated row is the first (and only) break regardless of timestamps.
    """
    h = _make_harness()
    for event in events:
        h.sink.append(event)

    event_ids = _chained_event_ids(h)
    target_idx = data.draw(st.integers(min_value=0, max_value=len(event_ids) - 1))
    target_event_id = event_ids[target_idx]

    # Use a bound SQLAlchemy update (not raw text) so UUID coercion works on
    # SQLite, where Uuid() stores without dashes and str() binds miss the row.
    tbl = h.tables.audit_events
    with h.session_factory.begin() as session:
        session.execute(
            tbl.update()
            .where(tbl.c.event_id == target_event_id)
            .values(subject_ref="mutated-by-property-test")
        )

    result = h.verifier.verify()
    assert result.verified is False
    assert result.first_broken_event_id == UUID(str(target_event_id))


# ---------------------------------------------------------------------------
# Property (c): non-monotonic / backdated appends still verify
# ---------------------------------------------------------------------------


@given(events=_event_lists)
@settings(deadline=None)
def test_non_monotonic_occurred_at_still_verifies(events: list[AuditEvent]) -> None:
    """Backdated / clock-skewed appends verify clean (insertion-keyed chain).

    Regression for the reviewer's false alarm: when occurred_at runs backwards
    relative to insertion order, the old occurred_at-ordered verifier reordered
    the rows and broke the chain. The chain is now a pure insertion linked list,
    so the walk follows prior_hash pointers and verifies no matter how the
    timestamps are arranged.

    To guarantee non-monotonicity in every example (not just the drawn ones), a
    final event is appended LAST with the earliest occurred_at of the whole
    sequence — the worst case for any time-ordered verifier. The whole trail
    must still verify.
    """
    h = _make_harness()
    for event in events:
        h.sink.append(event)

    # Append last but timestamp it earliest — insertion order and time order
    # now provably disagree at the tail.
    earliest = min(event.occurred_at for event in events) - timedelta(seconds=1)
    h.sink.append(
        AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.ERASURE_REQUESTED,
            subject_ref="backdated-tail",
            occurred_at=earliest,
            payload={},
        )
    )

    result = h.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


@given(events=_event_lists, data=st.data())
@settings(deadline=None)
def test_non_monotonic_then_single_mutation_breaks_at_mutated_row(
    events: list[AuditEvent], data: st.DataObject
) -> None:
    """A content tamper is localized to the mutated row even when timestamps
    are non-monotonic vs insertion order.
    """
    h = _make_harness()
    for event in events:
        h.sink.append(event)

    event_ids = _chained_event_ids(h)
    target_idx = data.draw(st.integers(min_value=0, max_value=len(event_ids) - 1))
    target_event_id = event_ids[target_idx]

    tbl = h.tables.audit_events
    with h.session_factory.begin() as session:
        session.execute(
            tbl.update()
            .where(tbl.c.event_id == target_event_id)
            .values(subject_ref="tampered-non-monotonic")
        )

    result = h.verifier.verify()
    assert result.verified is False
    assert result.first_broken_event_id == UUID(str(target_event_id))
