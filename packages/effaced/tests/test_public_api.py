"""The public surface stays importable and complete."""

from __future__ import annotations

import effaced
import effaced.manifest


def test_all_names_resolve() -> None:
    for name in effaced.__all__:
        assert getattr(effaced, name, None) is not None, f"effaced.{name} missing"


def test_all_is_sorted() -> None:
    """RUF022's isort-style order: SCREAMING_CASE constants first, then ASCII."""
    expected = sorted(effaced.__all__, key=lambda name: (not name.isupper(), name))
    assert list(effaced.__all__) == expected


def test_manifest_models_reexported_from_root() -> None:
    assert "ColumnEntry" in effaced.__all__
    assert effaced.ColumnEntry is effaced.manifest.ColumnEntry


def test_version_is_pep440ish() -> None:
    assert effaced.__version__
    assert effaced.__version__[0].isdigit()
