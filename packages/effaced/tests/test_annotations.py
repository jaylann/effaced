"""Annotation models validate their legal invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from effaced import (
    ErasureStrategy,
    PiiCategory,
    PiiSpec,
    RetentionPolicy,
    SubjectRef,
    pii,
)
from effaced.adapters.sqlalchemy import INFO_KEY


def test_pii_returns_spec_under_info_key() -> None:
    info = pii(PiiCategory.CONTACT)
    spec = info[INFO_KEY]
    assert isinstance(spec, PiiSpec)
    assert spec.category is PiiCategory.CONTACT
    assert spec.erasure is ErasureStrategy.DELETE


def test_retain_without_policy_is_rejected() -> None:
    with pytest.raises(ValidationError, match="RetentionPolicy"):
        PiiSpec(category=PiiCategory.FINANCIAL, erasure=ErasureStrategy.RETAIN)


def test_retain_with_policy_is_accepted() -> None:
    spec = PiiSpec(
        category=PiiCategory.FINANCIAL,
        erasure=ErasureStrategy.RETAIN,
        retention=RetentionPolicy(reason="§147 AO"),
    )
    assert spec.retention is not None
    assert spec.retention.reason == "§147 AO"


def test_specs_are_immutable() -> None:
    spec = PiiSpec(category=PiiCategory.CONTACT)
    with pytest.raises(ValidationError):
        spec.category = PiiCategory.IDENTITY  # type: ignore[misc]


def test_subject_ref_rejects_empty_identifiers() -> None:
    with pytest.raises(ValidationError):
        SubjectRef(kind="", value="cus_123")
    with pytest.raises(ValidationError):
        SubjectRef(kind="stripe_customer", value="")
