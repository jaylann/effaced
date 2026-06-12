"""Property-based guarantee: status always derives from the latest record."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import RecordingAuditSink
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import ConsentLedger, ConsentRecord, bind_tables

pytestmark = pytest.mark.property

PURPOSES = ("newsletter", "analytics", "ads")

grant_withdraw_sequences = st.lists(
    st.tuples(
        st.sampled_from(PURPOSES),
        st.booleans(),
        st.integers(min_value=0, max_value=10_000),
    ),
    max_size=20,
    unique_by=lambda event: event[2],
)


@given(events=grant_withdraw_sequences)
def test_status_derives_from_latest_record_per_purpose(
    events: list[tuple[str, bool, int]],
) -> None:
    """For every purpose, status equals the granted flag of the newest record."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    ledger = ConsentLedger(tables.consent_records, RecordingAuditSink())
    base = datetime(2026, 1, 1)
    records = [
        ConsentRecord(
            subject_id="subject",
            purpose=purpose,
            policy_version="v1",
            granted=granted,
            recorded_at=base + timedelta(seconds=offset),
        )
        for purpose, granted, offset in events
    ]
    with sessionmaker(engine)() as session:
        for record in records:
            ledger.record(session, record)
        session.commit()
        for purpose in PURPOSES:
            matching = [record for record in records if record.purpose == purpose]
            expected = (
                max(matching, key=lambda record: record.recorded_at).granted if matching else False
            )
            assert ledger.status(session, "subject", purpose) is expected
        chronological = tuple(sorted(records, key=lambda record: record.recorded_at))
        assert ledger.history(session, "subject") == chronological
        for purpose in PURPOSES:
            matching = [record for record in records if record.purpose == purpose]
            if not matching:
                continue
            # As-of the subject's last record, status_as_of must equal status.
            latest_at = max(record.recorded_at for record in matching)
            assert ledger.status_as_of(session, "subject", purpose, latest_at) is ledger.status(
                session, "subject", purpose
            )
    engine.dispose()
