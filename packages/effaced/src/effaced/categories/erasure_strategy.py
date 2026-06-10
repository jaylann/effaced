"""The :class:`ErasureStrategy` vocabulary."""

from __future__ import annotations

from enum import StrEnum


class ErasureStrategy(StrEnum):
    """What happens to a value when its subject is erased."""

    DELETE = "delete"
    """Remove the record (or null the field) outright."""

    ANONYMIZE = "anonymize"
    """Replace the value with an irreversible surrogate; the record survives."""

    RETAIN = "retain"
    """Keep the value untouched — it is under a legal retention duty.

    Retained fields MUST carry a :class:`~effaced.annotations.RetentionPolicy`
    explaining the duty; the audit trail records the retention decision.
    """
