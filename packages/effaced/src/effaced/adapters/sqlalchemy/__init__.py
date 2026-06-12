"""SQLAlchemy adapter — the first authoring layer for the effaced core.

The core (annotations, manifest, engine) is storage-agnostic; this package
is the thin layer that knows SQLAlchemy: authoring helpers that ride the
``info`` dict, a collector that derives the manifest from metadata, a
resolver that turns subject-link paths into a subject graph, the
anonymizer surrogate registry, the erasure executor that runs local steps,
the completeness linter that flags what the manifest does not cover, the
effaced-owned storage tables mounted via :func:`bind_tables`, and the
:class:`EffacedStack` facade that wires every engine from one base.
"""

from effaced.adapters.sqlalchemy.anonymizer import SurrogateRegistry, default_surrogate_registry
from effaced.adapters.sqlalchemy.collector import collect_data_map
from effaced.adapters.sqlalchemy.completeness_linter import lint_completeness
from effaced.adapters.sqlalchemy.effaced_stack import EffacedStack
from effaced.adapters.sqlalchemy.erasure_executor import ErasureExecutor
from effaced.adapters.sqlalchemy.info import INFO_KEY, pii, subject_link
from effaced.adapters.sqlalchemy.rectification_executor import RectificationExecutor
from effaced.adapters.sqlalchemy.resolution import resolve_subject_graph
from effaced.adapters.sqlalchemy.sql_status_counts_source import SqlStatusCountsSource
from effaced.adapters.sqlalchemy.storage import EffacedTables, bind_tables

__all__ = [
    "INFO_KEY",
    "EffacedStack",
    "EffacedTables",
    "ErasureExecutor",
    "RectificationExecutor",
    "SqlStatusCountsSource",
    "SurrogateRegistry",
    "bind_tables",
    "collect_data_map",
    "default_surrogate_registry",
    "lint_completeness",
    "pii",
    "resolve_subject_graph",
    "subject_link",
]
