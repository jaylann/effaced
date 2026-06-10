"""SQLAlchemy adapter — the first authoring layer for the effaced core.

The core (annotations, manifest, engine) is storage-agnostic; this package
is the thin layer that knows SQLAlchemy: authoring helpers that ride the
``info`` dict, a collector that derives the manifest from metadata, and a
resolver that turns subject-link paths into a subject graph.
"""

from effaced.adapters.sqlalchemy.collector import collect_data_map
from effaced.adapters.sqlalchemy.info import INFO_KEY, pii, subject_link
from effaced.adapters.sqlalchemy.resolution import resolve_subject_graph

__all__ = ["INFO_KEY", "collect_data_map", "pii", "resolve_subject_graph", "subject_link"]
