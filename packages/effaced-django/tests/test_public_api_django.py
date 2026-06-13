"""Every name in the package's ``__all__`` is importable."""

from __future__ import annotations

import effaced_django


def test_all_names_are_exported() -> None:
    for name in effaced_django.__all__:
        assert hasattr(effaced_django, name), name


def test_all_is_sorted() -> None:
    assert list(effaced_django.__all__) == sorted(effaced_django.__all__)
