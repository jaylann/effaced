"""Reflect a live database's schema into SQLAlchemy ``MetaData``.

The bridge from a serialized/authored manifest to executable engines: a
manifest names the tables that hold personal data, and this helper reflects
*exactly* those tables off a live connection so the FK-based resolver
(:func:`~effaced.adapters.sqlalchemy.resolve_subject_graph_from_fk`) can build
the subject graph from real foreign-key constraints — no ORM registry, no
hand-built metadata. Reflection issues read-only catalog queries only; it
never emits DDL or DML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import MetaData

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Engine


def reflect_metadata(engine: Engine, *, only: Sequence[str] | None = None) -> MetaData:
    """Reflect a database's tables into a fresh ``MetaData``.

    Wraps :meth:`sqlalchemy.MetaData.reflect`. When ``only`` is given, just
    those tables (and the foreign keys among them) are reflected, so
    unrelated tables in the same database never enter the subject graph —
    pass the manifest's table names to scope reflection to the declared
    surface. Omitting ``only`` reflects every table the engine can see.

    The reflection runs read-only catalog queries against the live
    connection; it issues no DDL and no DML.

    Args:
        engine: A connectable engine bound to the live database.
        only: Table names to reflect; ``None`` reflects all tables.

    Returns:
        A fresh ``MetaData`` holding the reflected tables and their
        foreign-key constraints.
    """
    metadata = MetaData()
    metadata.reflect(bind=engine, only=list(only) if only is not None else None)
    return metadata
