"""Property tests for the audit hash chain (ADR 0028).

Two properties:
  (a) UNTOUCHED: any sequence appended through the sink always verifies.
  (b) SINGLE-FIELD MUTATION: mutating exactly one stored field of one row
      causes verify() to report the mutated row as the first break (or an
      earlier one if ordering ambiguity exists — avoided by using strictly
      increasing timestamps).
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

_subject_refs = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "And"),
        whitelist_characters="-_",
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

_BASE_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _make_events(
    subject_refs: list[str], payloads: list[dict[str, str | int | bool]]
) -> list[AuditEvent]:
    """Build a list of events with strictly increasing timezone-aware timestamps.

    Aware (UTC) timestamps are used. compute_event_hash normalizes occurred_at
    to the UTC instant (see _canonical_occurred_at), so the digest is identical
    whether SQLite returns the column naive on read-back (it drops tzinfo) or
    psycopg returns it aware — append-time and verify-time agree either way.
    Strictly increasing timestamps keep the canonical (occurred_at, event_id)
    order unambiguous so a mutated row maps to a single first break.
    """
    n = len(subject_refs)
    return [
        AuditEvent(
            event_id=uuid4(),
            event_type=_EVENT_TYPES[i % len(_EVENT_TYPES)],
            subject_ref=subject_refs[i],
            occurred_at=_BASE_DT + timedelta(seconds=i + 1),
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
        )
    )
    .map(lambda t: _make_events(t[0], t[1]))
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


# ---------------------------------------------------------------------------
# Property (a): untouched chain always verifies
# ---------------------------------------------------------------------------


@given(events=_event_lists)
@settings(deadline=None)
def test_untouched_chain_always_verifies(events: list[AuditEvent]) -> None:
    """Any sequence appended through the sink verifies clean."""
    h = _make_harness()
    for event in events:
        h.sink.append(event)
    result = h.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


# ---------------------------------------------------------------------------
# Property (b): single-field mutation is always detected
# ---------------------------------------------------------------------------


def _read_rows_in_order(h: ChainHarness) -> list[dict[str, object]]:
    cols = h.tables.audit_events.c
    stmt = h.tables.audit_events.select().order_by(cols.occurred_at.asc(), cols.event_id.asc())
    with h.session_factory() as session:
        return [dict(row) for row in session.execute(stmt).mappings().all()]


@given(events=_event_lists, data=st.data())
@settings(deadline=None)
def test_single_field_mutation_is_detected_at_mutated_row(
    events: list[AuditEvent], data: st.DataObject
) -> None:
    """Mutating exactly one field of one stored row causes verify() to report
    that row (or an earlier one — impossible here because timestamps are
    strictly increasing, so the canonical order is unambiguous).
    """
    h = _make_harness()
    for event in events:
        h.sink.append(event)

    rows = _read_rows_in_order(h)
    # pick a row to mutate
    target_idx = data.draw(st.integers(min_value=0, max_value=len(rows) - 1))
    target_row = rows[target_idx]
    target_event_id = target_row["event_id"]

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
    # The reported break must be the mutated row (timestamps are strictly
    # increasing so no tie ambiguity; the mutated row's own stored hash
    # no longer matches recomputed hash, so it is the first break).
    assert result.first_broken_event_id == UUID(str(target_event_id))
