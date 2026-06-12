"""Backup replay — re-apply the erasures a database restore resurrected.

Restoring a backup brings back every subject erased after the backup
point. The append-only audit trail records which erasures were committed
in that window; this package derives a :class:`ReplayPlan` from a
*surviving* copy of the trail (the restored database's own trail lost the
window) and re-runs each erasure through the existing
:class:`~effaced.ErasurePlanner` — a mechanism for converging after a
restore, never a determination that anything is compliant. Semantics are
pinned in ADR 0018.
"""

from effaced.replay.plan import ReplayPlan, ReplayPlanEntry
from effaced.replay.replayer import Replayer
from effaced.replay.source import ReplaySource

__all__ = [
    "ReplayPlan",
    "ReplayPlanEntry",
    "ReplaySource",
    "Replayer",
]
