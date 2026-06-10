"""The public surface stays importable and complete."""

from __future__ import annotations

import effaced


def test_all_names_resolve() -> None:
    for name in effaced.__all__:
        assert getattr(effaced, name, None) is not None, f"effaced.{name} missing"


def test_version_is_pep440ish() -> None:
    assert effaced.__version__
    assert effaced.__version__[0].isdigit()
