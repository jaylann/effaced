"""Supabase error taxonomy — which failures retry and which abandon.

The saga runner retries any exception that is not a
:class:`~effaced.exceptions.ResolverError`, so resolver methods translate
admin-API responses as follows (404 is handled by callers before this
taxonomy is applied — an absent subject is success, never an error):

==========================  =================================================
Response                    Treatment
==========================  =================================================
``2xx``                     Success; nothing raised.
``4xx`` except 404 and 429  :class:`ResolverError` — bad key, missing admin
                            access, or malformed request; the same request
                            can never succeed on retry.
``429``                     Propagates as ``httpx.HTTPStatusError`` — saga
                            backoff is the cure.
``5xx``                     Propagates as ``httpx.HTTPStatusError`` —
                            Supabase-side fault; retry, then abandon.
connection faults           Propagate as ``httpx.TransportError`` subclasses.
==========================  =================================================

Translation keys on the status code only — GoTrue error-body shapes vary
across versions; the status is the stable part.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.exceptions import ResolverError

if TYPE_CHECKING:
    import httpx

_RATE_LIMITED = 429
_CLIENT_ERROR_FLOOR = 400
_SERVER_ERROR_FLOOR = 500


def raise_for_taxonomy(response: httpx.Response, action: str) -> None:
    """Apply the taxonomy to a non-404 admin-API response.

    Args:
        response: The admin-API response; callers handle 404 before this.
        action: Short verb for the message (``"export"``, ``"erasure"``)
            — never the subject reference, which must not reach
            exception text.

    Raises:
        ResolverError: A non-retryable 4xx — retrying cannot succeed.
        httpx.HTTPStatusError: 429 or a 5xx — propagates untranslated so
            the saga runner retries.
    """
    if response.is_success:
        return
    status = response.status_code
    if _CLIENT_ERROR_FLOOR <= status < _SERVER_ERROR_FLOOR and status != _RATE_LIMITED:
        raise ResolverError(f"supabase auth rejected the {action} request (status={status})")
    response.raise_for_status()
