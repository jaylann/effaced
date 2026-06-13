"""Errors raised while translating Django models into effaced metadata."""

from __future__ import annotations


class EffacedDjangoError(Exception):
    """A Django model could not be translated into effaced metadata.

    Raised loudly (never swallowed) when a field type has no SQLAlchemy
    equivalent the engine can scope or anonymize — guessing would risk
    erasing the wrong data.
    """
