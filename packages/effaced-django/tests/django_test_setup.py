"""Configure a minimal Django on import, before any model class is defined.

This lives in a plain module (not ``conftest.py``) on purpose: the core test
suite does ``from conftest import ...`` by bare module name, and a second
``conftest.py`` in another tests directory would shadow it in the shared
import namespace. Importing this module runs ``settings.configure`` +
``django.setup`` once; the adapter reads declared model metadata, never a live
Django database, so an in-memory SQLite default suffices.
"""

from __future__ import annotations

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        USE_TZ=True,
    )
    django.setup()
