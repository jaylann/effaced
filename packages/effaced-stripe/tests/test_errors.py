"""``is_resource_missing`` recognises the absent-subject case on either signal.

Stripe answers "no such object" with code ``resource_missing`` *and* HTTP
404, but the resolver's idempotency (404-on-delete is success) must not
hinge on both arriving together. These tests drive each arm of the
disjunction in isolation — the HTTP-boundary fake always sets both, so
only here is the ``or`` actually exercised.
"""

from __future__ import annotations

from stripe import InvalidRequestError

from effaced_stripe.errors import is_resource_missing


def _error(code: str | None, http_status: int) -> InvalidRequestError:
    return InvalidRequestError("boom", param="id", code=code, http_status=http_status)


def test_resource_missing_code_alone_is_absent() -> None:
    """The code arm: ``resource_missing`` without a 404 still means absent."""
    assert is_resource_missing(_error(code="resource_missing", http_status=400)) is True


def test_http_404_alone_is_absent() -> None:
    """The status arm: a 404 with any other code still means absent."""
    assert is_resource_missing(_error(code="parameter_unknown", http_status=404)) is True


def test_other_invalid_request_is_not_absent() -> None:
    """Neither arm: a non-404 with an unrelated code is a real rejection."""
    assert is_resource_missing(_error(code="parameter_unknown", http_status=400)) is False
