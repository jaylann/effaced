"""ErasureVerifier — read-only post-erasure read-back, honest verdict, audited."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple

import pytest
from conftest import Base, RecordingAuditSink, seed_two_subjects
from pydantic import ValidationError
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    EffacedTables,
    ErasurePlanner,
    ErasureVerification,
    ErasureVerifier,
    Outbox,
    ResolverRegistry,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor

ALLOWED_PAYLOAD_KEYS = {"tables_checked", "residual_rows", "surviving_rows", "failed_tables"}


class Harness(NamedTuple):
    """A planner + verifier over the seeded two-subject schema."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    planner: ErasurePlanner
    verifier: ErasureVerifier
    verify_sink: RecordingAuditSink


def build_harness() -> Harness:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    Base.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        seed_two_subjects(session)
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    planner = ErasurePlanner(
        data_map,
        graph,
        ResolverRegistry(),
        executor=ErasureExecutor(Base.metadata),
        outbox=Outbox(session_factory, tables.outbox),
        audit_sink=RecordingAuditSink(),
    )
    verify_sink = RecordingAuditSink()
    verifier = ErasureVerifier(data_map, graph, Base.metadata, audit_sink=verify_sink)
    return Harness(session_factory, tables, planner, verifier, verify_sink)


@pytest.fixture()
def harness() -> Harness:
    return build_harness()


def erase_subject_1(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1")
        session.commit()


def test_after_erasure_verified_is_true_and_residuals_zero(harness: Harness) -> None:
    erase_subject_1(harness)
    with harness.session_factory() as session:
        verification = harness.verifier.verify_subject_erased(session, "1")
    assert isinstance(verification, ErasureVerification)
    assert verification.verified is True
    assert verification.residual == {"comments": 0, "order_items": 0, "orders": 0}
    assert all(count == 0 for count in verification.residual.values())
    assert verification.verified_at.utcoffset() is not None


def test_surviving_tables_are_reported_and_never_flip_verified(harness: Harness) -> None:
    erase_subject_1(harness)
    with harness.session_factory() as session:
        verification = harness.verifier.verify_subject_erased(session, "1")
    # users is anonymized in place, invoices retained — both survive by design.
    assert verification.surviving == {"users": 1, "invoices": 1}
    assert verification.verified is True


def test_clean_verification_emits_one_erasure_verified_event(harness: Harness) -> None:
    erase_subject_1(harness)
    with harness.session_factory() as session:
        harness.verifier.verify_subject_erased(session, "1")
    assert len(harness.verify_sink.events) == 1
    event = harness.verify_sink.events[0]
    assert event.event_type == AuditEventType.ERASURE_VERIFIED
    assert event.subject_ref == "1"
    assert event.payload == {
        "tables_checked": 5,
        "residual_rows": 0,
        "surviving_rows": 2,
        "failed_tables": "",
    }


def test_audit_payload_carries_only_the_allowed_scalar_keys(harness: Harness) -> None:
    erase_subject_1(harness)
    with harness.session_factory() as session:
        harness.verifier.verify_subject_erased(session, "1")
    payload = harness.verify_sink.events[0].payload
    assert set(payload) == ALLOWED_PAYLOAD_KEYS
    assert all(isinstance(value, int | str) for value in payload.values())


def test_resurrected_row_flips_verified_and_names_the_table(harness: Harness) -> None:
    erase_subject_1(harness)
    with harness.session_factory() as session:
        # A trigger / ORM event / partial commit resurrects a deleted row.
        session.execute(Base.metadata.tables["orders"].insert().values(id=1, user_id=1))
        session.commit()
    with harness.session_factory() as session:
        verification = harness.verifier.verify_subject_erased(session, "1")
    assert verification.verified is False
    assert verification.residual["orders"] == 1
    assert verification.residual["comments"] == 0
    event = harness.verify_sink.events[0]
    assert event.event_type == AuditEventType.ERASURE_VERIFICATION_FAILED
    assert event.payload["failed_tables"] == "orders"
    assert event.payload["residual_rows"] == 1
    assert set(event.payload) == ALLOWED_PAYLOAD_KEYS


def test_pre_erasure_verification_is_honestly_false_with_counts(harness: Harness) -> None:
    """Before any erasure the row-deleted tables are full — verified is False."""
    with harness.session_factory() as session:
        verification = harness.verifier.verify_subject_erased(session, "1")
    assert verification.verified is False
    assert verification.residual == {"comments": 2, "order_items": 1, "orders": 1}
    assert verification.surviving == {"users": 1, "invoices": 1}
    assert harness.verify_sink.events[0].event_type == AuditEventType.ERASURE_VERIFICATION_FAILED
    assert harness.verify_sink.events[0].payload["residual_rows"] == 4


def test_verification_is_read_only(harness: Harness) -> None:
    """The verifier issues SELECT counts only — no row count changes."""
    erase_subject_1(harness)
    with harness.session_factory() as session:
        before = {
            name: len(session.execute(select(Base.metadata.tables[name])).all())
            for name in ("users", "invoices", "orders", "comments", "order_items")
        }
    with harness.session_factory() as session:
        harness.verifier.verify_subject_erased(session, "1")
        session.commit()
    with harness.session_factory() as session:
        after = {
            name: len(session.execute(select(Base.metadata.tables[name])).all())
            for name in ("users", "invoices", "orders", "comments", "order_items")
        }
    assert after == before


def test_no_cross_subject_bleed_in_counts(harness: Harness) -> None:
    """Subject 2's rows never count toward subject 1's verdict, or vice versa."""
    erase_subject_1(harness)
    with harness.session_factory() as session:
        # Subject 2 is untouched: its row-deleted tables are still full.
        two = harness.verifier.verify_subject_erased(session, "2")
    assert two.verified is False
    assert two.residual == {"comments": 1, "order_items": 1, "orders": 1}


def test_verified_must_agree_with_residual_counts() -> None:
    """The model rejects a verdict that contradicts its own residual counts."""
    with pytest.raises(ValidationError, match="verified must equal"):
        ErasureVerification(
            subject_id="1",
            verified_at=datetime.now(UTC),
            verified=True,
            residual={"orders": 1},
        )
    # The honest pairing validates fine.
    ok = ErasureVerification(
        subject_id="1",
        verified_at=datetime.now(UTC),
        verified=False,
        residual={"orders": 1},
    )
    assert ok.verified is False
