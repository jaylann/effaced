"""The :class:`SagaWorker` — drains the outbox off the serving event loop."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from effaced import SagaRunner

logger = logging.getLogger(__name__)


class SagaWorker:
    """Drives :meth:`effaced.SagaRunner.run_once` on a daemon thread.

    ``run_once`` makes blocking database calls between awaits, so it must
    never run on a serving event loop (ADR 0006). This worker packages the
    sanctioned alternative: a daemon thread running its own private
    ``asyncio`` loop, claiming batches until stopped and polling gently
    while the queue is empty.

    A failing batch (database briefly down, resolver dependency
    unreachable) is logged and retried after the poll interval — the
    worker never dies silently, because a stalled drain loop means
    data-subject requests stop completing. Running several workers at
    once is safe: claiming uses ``FOR UPDATE SKIP LOCKED`` and crashed
    claims are re-claimed after the lease expires (ADR 0010).
    """

    def __init__(self, runner: SagaRunner, *, poll_interval: float = 5.0) -> None:
        """Wire the worker to the runner it drives.

        Args:
            runner: The :class:`effaced.SagaRunner` to drain — typically
                ``stack.saga_runner``.
            poll_interval: Seconds to sleep when a batch comes back empty
                or fails, before claiming again.
        """
        self._runner = runner
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the drain thread; a no-op if it is already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._drain_forever, name="effaced-saga-worker", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        """Signal the drain loop to exit and wait for the thread to finish.

        The loop notices the signal at its next batch boundary or sleep
        wake-up, so stopping can take up to one poll interval.

        Args:
            timeout: Seconds to wait for the thread to join; the thread is
                a daemon, so a missed join never blocks interpreter exit.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _drain_forever(self) -> None:
        """Thread target: run the drain loop on a private event loop."""
        asyncio.run(self._drain())

    async def _drain(self) -> None:
        """Claim batches until stopped; sleep while the queue is empty."""
        while not self._stop.is_set():
            try:
                drained = await self._runner.run_once()
            except Exception:
                logger.exception("outbox drain batch failed; retrying after poll interval")
                drained = 0
            if drained == 0:
                await asyncio.sleep(self._poll_interval)
