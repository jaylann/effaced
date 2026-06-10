"""The :class:`BackoffPolicy` model — retry schedule and claim lease."""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, model_validator


class BackoffPolicy(BaseModel):
    """Deterministic exponential backoff for outbox retries.

    The delay after the *n*-th attempt is
    ``min(base_delay * 2**(n - 1), max_delay)`` — no jitter, so tests can
    pin exact schedules; concurrent runners are already spread by
    ``SKIP LOCKED`` claiming.

    Attributes:
        base_delay: Delay after the first failed attempt.
        max_delay: Ceiling the doubling never exceeds.
        lease: How long a claim protects an ``IN_FLIGHT`` entry from other
            runners. Must comfortably exceed the slowest expected resolver
            call: a lease that expires mid-call lets another runner execute
            the same entry again — converging (resolvers are idempotent)
            but wasteful and noisy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(hours=1)
    lease: timedelta = timedelta(minutes=5)

    @model_validator(mode="after")
    def _positive_and_ordered(self) -> BackoffPolicy:
        """Reject non-positive durations and a ceiling below the base."""
        if self.base_delay <= timedelta(0):
            msg = f"base_delay must be positive, got {self.base_delay!r}"
            raise ValueError(msg)
        if self.lease <= timedelta(0):
            msg = f"lease must be positive, got {self.lease!r}"
            raise ValueError(msg)
        if self.max_delay < self.base_delay:
            msg = (
                f"max_delay ({self.max_delay!r}) must be at least base_delay ({self.base_delay!r})"
            )
            raise ValueError(msg)
        return self

    def delay(self, attempts: int) -> timedelta:
        """The wait before the next try after ``attempts`` tries so far.

        Args:
            attempts: How many times the entry has been attempted (≥ 1).

        Returns:
            ``min(base_delay * 2**(attempts - 1), max_delay)``.

        Raises:
            ValueError: If ``attempts`` is not positive.
        """
        if attempts < 1:
            msg = f"attempts must be >= 1, got {attempts}"
            raise ValueError(msg)
        delay = self.base_delay
        for _ in range(attempts - 1):
            if delay >= self.max_delay:
                break
            delay *= 2
        return min(delay, self.max_delay)
