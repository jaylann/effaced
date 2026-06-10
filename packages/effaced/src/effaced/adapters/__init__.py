"""Storage adapters — thin authoring layers over the storage-agnostic core.

Each adapter teaches effaced how to read annotations from (and eventually
operate on) one ORM/stack. SQLAlchemy ships first; others are added
demand-pulled, never pre-emptively.
"""
