"""The :func:`bind_tables` entry point for mounting effaced-owned tables."""

from __future__ import annotations

from sqlalchemy import MetaData

from effaced.adapters.sqlalchemy.storage.audit_events_table import (
    AUDIT_EVENTS_TABLE_NAME,
    build_audit_events_table,
)
from effaced.adapters.sqlalchemy.storage.consent_records_table import (
    CONSENT_RECORDS_TABLE_NAME,
    build_consent_records_table,
)
from effaced.adapters.sqlalchemy.storage.effaced_tables import EffacedTables
from effaced.adapters.sqlalchemy.storage.outbox_table import (
    OUTBOX_TABLE_NAME,
    build_outbox_table,
)
from effaced.adapters.sqlalchemy.storage.restriction_records_table import (
    RESTRICTION_RECORDS_TABLE_NAME,
    build_restriction_records_table,
)

_ALL_TABLE_NAMES = (
    AUDIT_EVENTS_TABLE_NAME,
    CONSENT_RECORDS_TABLE_NAME,
    OUTBOX_TABLE_NAME,
    RESTRICTION_RECORDS_TABLE_NAME,
)


def bind_tables(metadata: MetaData) -> EffacedTables:
    """Mount the effaced-owned tables on the application's ``MetaData``.

    Defines ``effaced_audit_events``, ``effaced_consent_records``,
    ``effaced_outbox`` and ``effaced_restriction_records`` so they live in
    *your* database and ride *your* migration tooling — no migration tool is
    assumed and no DDL is executed here. Calling it again on the same
    ``MetaData`` is a no-op returning the already-mounted tables, so
    module-level and app-factory setup styles both work.

    With Alembic, call this where your ``env.py``'s ``target_metadata`` is
    defined; ``alembic revision --autogenerate`` then picks the tables up
    like your own. New effaced releases may add tables, columns or indexes
    in MINOR versions — re-run autogenerate after upgrading. Owned-table
    changes are additive-only, and additive columns backfill populated
    tables via server defaults — the caller-owned migration contract is
    ADR 0021 (see the Alembic guide on the docs site). Without a
    migration tool, ``metadata.create_all(engine)`` creates them directly.

    Example:
        >>> from effaced import bind_tables
        >>> tables = bind_tables(Base.metadata)  # doctest: +SKIP
        >>> tables.audit_events.name  # doctest: +SKIP
        'effaced_audit_events'

    Args:
        metadata: The ``MetaData`` your migrations already manage
            (typically ``Base.metadata``).

    Returns:
        Handles to the four mounted tables.

    Raises:
        ValueError: If only some of the table names already exist on
            ``metadata`` — i.e. a table of your own collides with an
            ``effaced_``-prefixed name.
    """
    present = [name for name in _ALL_TABLE_NAMES if name in metadata.tables]
    if len(present) == len(_ALL_TABLE_NAMES):
        return EffacedTables(
            audit_events=metadata.tables[AUDIT_EVENTS_TABLE_NAME],
            consent_records=metadata.tables[CONSENT_RECORDS_TABLE_NAME],
            outbox=metadata.tables[OUTBOX_TABLE_NAME],
            restriction_records=metadata.tables[RESTRICTION_RECORDS_TABLE_NAME],
        )
    if present:
        joined = ", ".join(present)
        msg = (
            f"metadata already defines {joined} without the other effaced tables; "
            "the effaced_ prefix is reserved for tables mounted by bind_tables()"
        )
        raise ValueError(msg)
    return EffacedTables(
        audit_events=build_audit_events_table(metadata),
        consent_records=build_consent_records_table(metadata),
        outbox=build_outbox_table(metadata),
        restriction_records=build_restriction_records_table(metadata),
    )
