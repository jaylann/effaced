"""The :class:`OutboxOperation` vocabulary."""

from __future__ import annotations

from enum import StrEnum


class OutboxOperation(StrEnum):
    """Which external operation an outbox entry performs.

    Completion is tracked per (subject, operation): ``ERASURE_COMPLETED``
    considers only erase entries, ``RECTIFICATION_COMPLETED`` only rectify
    entries (ADR 0013). Adding members is a MINOR change; removing or
    renaming is MAJOR (stored rows must stay readable forever).
    """

    ERASE = "erase"
    """An Art. 17 erasure call (``Resolver.erase_subject``)."""

    RECTIFY = "rectify"
    """An Art. 16 rectification call (``RectifyingResolver.rectify_subject``)."""
