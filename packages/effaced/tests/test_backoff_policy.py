"""BackoffPolicy: deterministic doubling, a hard ceiling, validated knobs."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from effaced import BackoffPolicy


def test_defaults_double_from_thirty_seconds_to_an_hour() -> None:
    policy = BackoffPolicy()
    assert policy.delay(1) == timedelta(seconds=30)
    assert policy.delay(2) == timedelta(minutes=1)
    assert policy.delay(5) == timedelta(minutes=8)
    assert policy.delay(8) == timedelta(hours=1)  # 64min capped
    assert policy.delay(100) == timedelta(hours=1)


def test_custom_schedule() -> None:
    policy = BackoffPolicy(
        base_delay=timedelta(seconds=1),
        max_delay=timedelta(seconds=5),
        lease=timedelta(seconds=10),
    )
    assert policy.delay(1) == timedelta(seconds=1)
    assert policy.delay(2) == timedelta(seconds=2)
    assert policy.delay(3) == timedelta(seconds=4)
    assert policy.delay(4) == timedelta(seconds=5)
    assert policy.lease == timedelta(seconds=10)


def test_delay_never_decreases_and_never_exceeds_the_cap() -> None:
    policy = BackoffPolicy(base_delay=timedelta(seconds=7), max_delay=timedelta(minutes=3))
    delays = [policy.delay(attempts) for attempts in range(1, 20)]
    assert delays == sorted(delays)
    assert all(delay <= policy.max_delay for delay in delays)


def test_delay_rejects_non_positive_attempts() -> None:
    with pytest.raises(ValueError, match="attempts"):
        BackoffPolicy().delay(0)


@pytest.mark.parametrize(
    "knobs",
    [
        {"base_delay": timedelta(0)},
        {"base_delay": timedelta(seconds=-1)},
        {"lease": timedelta(0)},
        {"base_delay": timedelta(minutes=10), "max_delay": timedelta(minutes=1)},
    ],
)
def test_invalid_knobs_are_rejected(knobs: dict[str, timedelta]) -> None:
    with pytest.raises(ValidationError):
        BackoffPolicy(**knobs)


def test_policy_is_frozen() -> None:
    with pytest.raises(ValidationError):
        BackoffPolicy().base_delay = timedelta(seconds=1)  # type: ignore[misc]  # proving frozen
