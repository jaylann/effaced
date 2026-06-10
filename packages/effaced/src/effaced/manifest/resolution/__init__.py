"""Subject-link resolution — pure models and ordering, no storage library.

Turns declared :class:`~effaced.annotations.SubjectLink` paths into
executable per-table access plans and an FK-safe deletion order. This
package is pure data and algorithms; the SQLAlchemy mapper walking that
produces these models lives in :mod:`effaced.adapters.sqlalchemy`.
"""

from effaced.manifest.resolution.join_hop import JoinHop
from effaced.manifest.resolution.ordering import fk_safe_deletion_order
from effaced.manifest.resolution.subject_graph import SubjectGraph
from effaced.manifest.resolution.table_access_plan import TableAccessPlan

__all__ = ["JoinHop", "SubjectGraph", "TableAccessPlan", "fk_safe_deletion_order"]
