"""The sync/async boundaries stay where ADR 0006 fixed them."""

from __future__ import annotations

import inspect

from effaced import (
    ConsentLedger,
    DatabaseAuditSink,
    ErasurePlanner,
    Exporter,
    Outbox,
    Resolver,
    SagaRunner,
)

SYNC_ENGINE_METHODS = (
    Exporter.export_subject,
    ErasurePlanner.plan,
    ErasurePlanner.erase_subject,
    ConsentLedger.record,
    ConsentLedger.status,
    ConsentLedger.history,
    DatabaseAuditSink.append,
    DatabaseAuditSink.read,
    Outbox.enqueue,
    Outbox.claim_batch,
)

ASYNC_EDGE_METHODS = (
    Resolver.export_subject,
    Resolver.erase_subject,
    SagaRunner.run_once,
)


def test_engine_api_is_sync() -> None:
    for method in SYNC_ENGINE_METHODS:
        assert not inspect.iscoroutinefunction(method), f"{method.__qualname__} must be sync"


def test_async_only_at_inherently_async_edges() -> None:
    for method in ASYNC_EDGE_METHODS:
        assert inspect.iscoroutinefunction(method), f"{method.__qualname__} must be async"
