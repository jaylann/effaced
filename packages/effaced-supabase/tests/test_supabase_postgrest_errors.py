"""The shared error taxonomy, exercised with the PostgREST ``system`` label.

`raise_for_taxonomy` is shared across the Supabase resolvers; these pin
the status-code partition and that the ``system`` label names the
rejecting surface in the :class:`ResolverError` message.
"""

from __future__ import annotations

import httpx
import pytest

from effaced.exceptions import ResolverError
from effaced_supabase.errors import raise_for_taxonomy


def _response(status: int) -> httpx.Response:
    return httpx.Response(
        status, request=httpx.Request("GET", "https://project.supabase.co/rest/v1/t")
    )


@pytest.mark.parametrize("status", [400, 401, 403, 409, 422])
def test_nonretryable_4xx_raise_resolver_error_naming_the_system(status: int) -> None:
    with pytest.raises(
        ResolverError, match=f"supabase postgrest rejected the export request \\(status={status}\\)"
    ):
        raise_for_taxonomy(_response(status), "export", system="supabase postgrest")


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_rate_limit_and_5xx_propagate_untranslated(status: int) -> None:
    with pytest.raises(httpx.HTTPStatusError) as error:
        raise_for_taxonomy(_response(status), "erasure", system="supabase postgrest")
    assert not isinstance(error.value, ResolverError)


@pytest.mark.parametrize("status", [200, 201, 204])
def test_success_raises_nothing(status: int) -> None:
    raise_for_taxonomy(_response(status), "export", system="supabase postgrest")
