"""The :class:`LegalBasis` vocabulary."""

from __future__ import annotations

from enum import StrEnum


class LegalBasis(StrEnum):
    """Art. 6(1) lawful bases for processing.

    Recorded per field/store so exports can state *why* data is held —
    required Art. 15(1)(a) metadata.
    """

    CONSENT = "consent"
    CONTRACT = "contract"
    LEGAL_OBLIGATION = "legal_obligation"
    VITAL_INTERESTS = "vital_interests"
    PUBLIC_TASK = "public_task"
    LEGITIMATE_INTERESTS = "legitimate_interests"
