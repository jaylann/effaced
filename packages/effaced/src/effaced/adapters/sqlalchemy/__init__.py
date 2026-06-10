"""SQLAlchemy adapter — the first authoring layer for the effaced core.

The core (annotations, manifest, engine) is storage-agnostic; this package
is the thin layer that knows SQLAlchemy: authoring helpers that ride the
``info`` dict, and a collector that derives the manifest from metadata.
"""

from effaced.adapters.sqlalchemy.collector import collect_data_map
from effaced.adapters.sqlalchemy.info import INFO_KEY, pii, subject_link

__all__ = ["INFO_KEY", "collect_data_map", "pii", "subject_link"]
