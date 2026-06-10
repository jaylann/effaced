"""The data map — a versioned manifest derived from model annotations."""

from effaced.manifest.column_entry import ColumnEntry
from effaced.manifest.data_map import DataMap
from effaced.manifest.migration import MANIFEST_SCHEMA_VERSION, migrate
from effaced.manifest.resolution import (
    JoinHop,
    SubjectGraph,
    TableAccessPlan,
    fk_safe_deletion_order,
)
from effaced.manifest.table_entry import TableEntry

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "ColumnEntry",
    "DataMap",
    "JoinHop",
    "SubjectGraph",
    "TableAccessPlan",
    "TableEntry",
    "fk_safe_deletion_order",
    "migrate",
]
