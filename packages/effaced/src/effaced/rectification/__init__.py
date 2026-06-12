"""Art. 16 rectification — category-keyed corrections, atomic locally, saga-driven externally.

Corrections are keyed by :class:`~effaced.PiiCategory`, never by column;
erasure strategy does not gate them (``RETAIN`` and ``ANONYMIZE`` columns
are rectified too); external fan-out reuses the outbox, whose payload is
cleared at terminal status; and the audit trail records names and counts
only — old and new values never appear in any event (ADR 0013).
"""

from effaced.rectification.rectifier import Rectifier
from effaced.rectification.result import RectificationResult
from effaced.rectification.step import RectificationStep
from effaced.rectification.step_executor import RectificationStepExecutor

__all__ = [
    "RectificationResult",
    "RectificationStep",
    "RectificationStepExecutor",
    "Rectifier",
]
