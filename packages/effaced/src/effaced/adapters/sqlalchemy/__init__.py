"""SQLAlchemy adapter — the first authoring layer for the effaced core.

The core (annotations, manifest, engine) is storage-agnostic; this package
is the thin layer that knows SQLAlchemy: authoring helpers that ride the
``info`` dict, a collector that derives the manifest from metadata, a
resolver that turns subject-link paths into a subject graph, the
anonymizer surrogate registry, and the effaced-owned storage tables
mounted via :func:`bind_tables`.
"""

from effaced.adapters.sqlalchemy.anonymizer import SurrogateRegistry, default_surrogate_registry
from effaced.adapters.sqlalchemy.collector import collect_data_map
from effaced.adapters.sqlalchemy.info import INFO_KEY, pii, subject_link
from effaced.adapters.sqlalchemy.resolution import resolve_subject_graph
from effaced.adapters.sqlalchemy.storage import EffacedTables, bind_tables

__all__ = [
    "INFO_KEY",
    "EffacedTables",
    "SurrogateRegistry",
    "bind_tables",
    "collect_data_map",
    "default_surrogate_registry",
    "pii",
    "resolve_subject_graph",
    "subject_link",
]
