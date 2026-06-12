"""Effaced-owned storage tables, mounted on the application's own metadata.

Consent records, restriction records, audit events and outbox entries live
in the *user's* database — zero data leaves their system in OSS mode.
:func:`bind_tables` defines the four ``effaced_``-prefixed tables on a
caller-supplied ``MetaData`` so they flow through the application's own
migrations
(Alembic autogenerate, or plain ``metadata.create_all``).
"""

from effaced.adapters.sqlalchemy.storage.bind_tables import bind_tables
from effaced.adapters.sqlalchemy.storage.effaced_tables import EffacedTables

__all__ = ["EffacedTables", "bind_tables"]
