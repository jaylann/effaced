"""The SagaWorker thread: drains, survives failures, stops, and rides the lifespan."""

from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_test_app import build_stack, make_engine

from effaced_fastapi import EffacedFastAPI, SagaWorker


class FakeRunner:
    """Counts run_once calls; pretends the queue drains after two batches."""

    def __init__(self) -> None:
        self.calls = 0
        self.drained = threading.Event()

    async def run_once(self) -> int:
        self.calls += 1
        if self.calls >= 2:
            self.drained.set()
            return 0
        return 1


class FailingRunner:
    """Raises on the first batch — the worker must log and keep going."""

    def __init__(self) -> None:
        self.calls = 0
        self.recovered = threading.Event()

    async def run_once(self) -> int:
        self.calls += 1
        if self.calls == 1:
            msg = "database briefly down"
            raise RuntimeError(msg)
        self.recovered.set()
        return 0


def wait_for(event: threading.Event, timeout: float = 5.0) -> None:
    assert event.wait(timeout), "worker did not reach the expected state in time"


def test_worker_drains_until_empty_and_stops() -> None:
    runner = FakeRunner()
    worker = SagaWorker(runner, poll_interval=0.01)  # type: ignore[arg-type]
    worker.start()
    wait_for(runner.drained)
    worker.stop()
    assert runner.calls >= 2


def test_worker_survives_a_failing_batch() -> None:
    runner = FailingRunner()
    worker = SagaWorker(runner, poll_interval=0.01)  # type: ignore[arg-type]
    worker.start()
    wait_for(runner.recovered)
    worker.stop()
    assert runner.calls >= 2


def test_start_is_idempotent_while_running() -> None:
    runner = FakeRunner()
    worker = SagaWorker(runner, poll_interval=0.01)  # type: ignore[arg-type]
    worker.start()
    worker.start()  # no second thread, no error
    wait_for(runner.drained)
    worker.stop()


def test_stop_before_start_is_a_noop() -> None:
    SagaWorker(FakeRunner(), poll_interval=0.01).stop()  # type: ignore[arg-type]


def test_lifespan_starts_and_stops_the_worker() -> None:
    """The packaged lifespan drains a real (empty) outbox and shuts down cleanly."""
    gdpr = EffacedFastAPI(stack=build_stack(make_engine()))
    app = FastAPI(lifespan=gdpr.lifespan(poll_interval=0.01))
    with TestClient(app) as client:
        assert client.app is not None
        time.sleep(0.05)  # let the worker claim at least one empty batch
    # exiting the context runs shutdown; reaching here means stop() joined
