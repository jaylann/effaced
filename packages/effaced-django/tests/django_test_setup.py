"""Configure a minimal Django for the adapter tests, before any model is defined.

This lives in a plain module (not ``conftest.py``) on purpose: the core test
suite does ``from conftest import ...`` by bare module name, and a second
``conftest.py`` in another tests directory would shadow it in the shared
import namespace. Test modules call :func:`ensure_configured` once before
defining models; the adapter reads declared model metadata, never a live
Django database, so an in-memory SQLite default suffices.
"""

from __future__ import annotations

import django
from django.conf import settings


def ensure_configured() -> None:
    """Configure Django + run app setup once; a no-op if already configured."""
    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            USE_TZ=True,
        )
        django.setup()
