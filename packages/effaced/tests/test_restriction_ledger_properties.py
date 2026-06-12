"""Property-based guarantees: status derivation and PII-free audit payloads."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import RecordingAuditSink
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import RestrictionLedger, RestrictionRecord, bind_tables

pytestmark = pytest.mark.property

PURPOSES = ("newsletter", "analytics", "ads")
SCOPES = (None, *PURPOSES)

# Offsets are deliberately NOT unique: exact-timestamp ties must resolve to
# the restricting record, and the naive model below encodes exactly that.
restriction_sequences = st.lists(
    st.tuples(
        st.sampled_from(SCOPES),
        st.booleans(),
        st.integers(min_value=0, max_value=100),
    ),
    max_size=20,
)


def naive_scope_status(records: list[RestrictionRecord], purpose: str | None) -> bool:
    """Latest record for one scope; among exact ties the restricting record wins."""
    matching = [record for record in records if record.purpose == purpose]
    if not matching:
        return False
    latest = max(record.recorded_at for record in matching)
    return any(record.restricted for record in matching if record.recorded_at == latest)


@given(events=restriction_sequences)
def test_status_equals_naive_rederivation(events: list[tuple[str | None, bool, int]]) -> None:
    """status() is latest-global OR latest-purpose, with restricted winning ties."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    ledger = RestrictionLedger(tables.restriction_records, RecordingAuditSink())
    base = datetime(2026, 1, 1)
    records = [
        RestrictionRecord(
            subject_id="subject",
            purpose=purpose,
            restricted=restricted,
            recorded_at=base + timedelta(seconds=offset),
        )
        for purpose, restricted, offset in events
    ]
    with sessionmaker(engine)() as session:
        for record in records:
            ledger.record(session, record)
        session.commit()
        assert ledger.status(session, "subject") is naive_scope_status(records, None)
        for purpose in PURPOSES:
            expected = naive_scope_status(records, None) or naive_scope_status(records, purpose)
            assert ledger.status(session, "subject", purpose) is expected
    engine.dispose()


@given(
    events=restriction_sequences,
    reason=st.text(min_size=1, max_size=40),
    source=st.text(min_size=1, max_size=40),
)
def test_audit_payloads_never_contain_reason_or_source(
    events: list[tuple[str | None, bool, int]], reason: str, source: str
) -> None:
    """Free-text fields never leak into the mirrored audit events."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    sink = RecordingAuditSink()
    ledger = RestrictionLedger(tables.restriction_records, sink)
    base = datetime(2026, 1, 1)
    with sessionmaker(engine)() as session:
        for purpose, restricted, offset in events:
            ledger.record(
                session,
                RestrictionRecord(
                    subject_id="subject",
                    purpose=purpose,
                    restricted=restricted,
                    reason=reason,
                    recorded_at=base + timedelta(seconds=offset),
                    source=source,
                ),
            )
        session.commit()
    assert len(sink.events) == len(events)
    for event in sink.events:
        assert "reason" not in event.payload
        assert "source" not in event.payload
    engine.dispose()
