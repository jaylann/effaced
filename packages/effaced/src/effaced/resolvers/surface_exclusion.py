"""The :class:`SurfaceExclusion` — a field a resolver explicitly does not reach."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SurfaceExclusion(BaseModel):
    """A field a resolver explicitly does *not* reach, with the reason why.

    An exclusion makes a known gap honest instead of implicit: a
    caller-defined metadata blob the resolver cannot read, an event
    payload the vendor retains beyond the deletion API, a value the
    external API never exposes. The ``field`` is an
    :func:`fnmatch.fnmatch` glob (same semantics as
    :class:`~effaced.CoveredField`); the conformance suite checks no
    exported record matches it, so an exclusion that is silently breached
    fails loudly.

    An exclusion is a *declaration*, never a compliance determination: it
    records what this resolver's mechanism does not cover, not that the
    excluded data is absent or lawful to retain.

    Attributes:
        field: The :func:`fnmatch.fnmatch` glob the resolver does not
            cover; no exported record may match it.
        reason: A human-readable reason the field is out of reach — why
            the gap exists, in plain words.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)
