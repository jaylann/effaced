"""Stripe error taxonomy — which failures retry and which abandon.

The saga runner retries any exception that is not a
:class:`~effaced.exceptions.ResolverError`, so the resolver translates
Stripe's exceptions as follows:

==============================  =============================================
Stripe exception                Treatment
==============================  =============================================
``InvalidRequestError`` (404 /  Subject is absent: export returns empty,
``resource_missing``)           erasure reports ``already_absent=True``.
``InvalidRequestError`` (other) :class:`ResolverError` — the same request
                                can never succeed on retry.
``AuthenticationError``         :class:`ResolverError` — bad or revoked key.
``PermissionError``             :class:`ResolverError` — the restricted key
                                lacks a required permission.
``RateLimitError``              Propagates — saga backoff is the cure.
``APIConnectionError``          Propagates — network blip.
``APIError`` / other            Propagates — Stripe-side fault; retry, then
                                abandon.
==============================  =============================================
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stripe import InvalidRequestError

_NOT_FOUND = 404


def is_resource_missing(error: InvalidRequestError) -> bool:
    """Whether Stripe answered "no such object" — the absent-subject case.

    Args:
        error: The ``InvalidRequestError`` Stripe raised.

    Returns:
        True when the request failed only because the object does not
        exist (HTTP 404 / code ``resource_missing``).
    """
    return error.code == "resource_missing" or error.http_status == _NOT_FOUND
