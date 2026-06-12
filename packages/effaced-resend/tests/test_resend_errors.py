"""The Resend error taxonomy: what abandons, what retries, what leaks.

Non-retryable 4xx responses raise ``ResolverError``; 429, 5xx, and
connection faults propagate untranslated for the saga runner to retry.
``ResolverError`` messages never carry the subject's email or the key.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fake_resend_transport import FakeResendTransport

from effaced import SubjectRef
from effaced.exceptions import ResolverError
from effaced_resend import ResendResolver

KEY = "re_secret_key"
EMAIL = "subject@example.com"
REF = SubjectRef(kind="resend", value=EMAIL)


def _failing_resolver(status: int) -> ResendResolver:
    return ResendResolver(KEY, transport=FakeResendTransport(error_status=status))


@pytest.mark.parametrize("status", [400, 401, 403, 405, 409, 422, 451])
def test_nonretryable_4xx_raises_resolver_error(status: int) -> None:
    resolver = _failing_resolver(status)
    with pytest.raises(ResolverError):
        asyncio.run(resolver.export_subject(REF))
    with pytest.raises(ResolverError):
        asyncio.run(resolver.erase_subject(REF))


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_statuses_propagate_untranslated(status: int) -> None:
    resolver = _failing_resolver(status)
    with pytest.raises(httpx.HTTPStatusError) as export_error:
        asyncio.run(resolver.export_subject(REF))
    with pytest.raises(httpx.HTTPStatusError) as erase_error:
        asyncio.run(resolver.erase_subject(REF))
    assert not isinstance(export_error.value, ResolverError)
    assert not isinstance(erase_error.value, ResolverError)


def test_connection_faults_propagate_untranslated() -> None:
    fake = FakeResendTransport(connection_error=True)
    resolver = ResendResolver(KEY, transport=fake)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(resolver.export_subject(REF))
    with pytest.raises(httpx.ConnectError):
        asyncio.run(resolver.erase_subject(REF))


def test_resolver_error_messages_leak_neither_email_nor_key() -> None:
    resolver = _failing_resolver(401)
    with pytest.raises(ResolverError) as error:
        asyncio.run(resolver.export_subject(REF))
    message = str(error.value)
    assert EMAIL not in message
    assert KEY not in message
    assert "401" in message
