"""SagaRunner wiring — run_once is an explicit not-yet-implemented stub."""

from __future__ import annotations

import asyncio

import pytest
from conftest import RecordingAuditSink
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import Outbox, ResolverRegistry, SagaRunner, bind_tables


def test_run_once_is_not_yet_implemented() -> None:
    """A fully wired runner constructs fine and refuses run_once loudly."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    runner = SagaRunner(
        ResolverRegistry(),
        Outbox(sessionmaker(engine), tables.outbox),
        RecordingAuditSink(),
        max_attempts=3,
    )
    with pytest.raises(NotImplementedError):
        asyncio.run(runner.run_once())
    engine.dispose()
