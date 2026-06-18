"""The public surface stays importable and complete."""

from __future__ import annotations

import effaced
import effaced.adapters.sqlalchemy
import effaced.annotations
import effaced.manifest
import effaced.saga


def test_all_names_resolve() -> None:
    for name in effaced.__all__:
        assert getattr(effaced, name, None) is not None, f"effaced.{name} missing"


def test_status_counts_source_reexported_from_root() -> None:
    assert "StatusCountsSource" in effaced.__all__
    assert effaced.StatusCountsSource is effaced.saga.StatusCountsSource
    assert "SqlStatusCountsSource" in effaced.__all__
    assert effaced.SqlStatusCountsSource is effaced.adapters.sqlalchemy.SqlStatusCountsSource


def test_all_is_sorted() -> None:
    """RUF022's isort-style order: SCREAMING_CASE constants first, then ASCII."""
    expected = sorted(effaced.__all__, key=lambda name: (not name.isupper(), name))
    assert list(effaced.__all__) == expected


def test_manifest_models_reexported_from_root() -> None:
    assert "ColumnEntry" in effaced.__all__
    assert effaced.ColumnEntry is effaced.manifest.ColumnEntry


def test_composite_subject_identity_reexported_from_root() -> None:
    """The composite-subject-key public surface (ADR 0025) is exported."""
    for name in (
        "CompositeSubjectId",
        "SubjectIdentifier",
        "canonical_subject_id",
        "parse_canonical",
    ):
        assert name in effaced.__all__, f"effaced.{name} missing from __all__"
    assert effaced.CompositeSubjectId is effaced.annotations.CompositeSubjectId
    assert effaced.canonical_subject_id is effaced.annotations.canonical_subject_id
    assert effaced.parse_canonical is effaced.annotations.parse_canonical
    # SubjectIdentifier is the str | CompositeSubjectId alias.
    assert effaced.SubjectIdentifier == (str | effaced.CompositeSubjectId)


def test_version_is_pep440ish() -> None:
    assert effaced.__version__
    assert effaced.__version__[0].isdigit()
