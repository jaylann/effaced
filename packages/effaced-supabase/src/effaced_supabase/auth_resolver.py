"""The :class:`SupabaseAuthResolver` — the subject's ``auth.users`` record."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from effaced.resolvers import ResolverErasure, ResolverExport
from effaced_supabase.auth_covered_surface import AUTH_COVERED_SURFACE
from effaced_supabase.auth_export_records import user_records
from effaced_supabase.errors import raise_for_taxonomy

if TYPE_CHECKING:
    from effaced import CoveredSurface
    from effaced.annotations import SubjectRef

_NOT_FOUND = 404
_DEFAULT_TIMEOUT = 10.0


class SupabaseAuthResolver:
    """Exports and erases a subject's Supabase Auth (``auth.users``) record.

    Expects refs of kind ``"supabase_auth"`` (refs are routed to the
    resolver whose name equals their kind — ADR 0008) whose value is the
    GoTrue user id. Talks to the Admin API (``/auth/v1/admin``), which
    only accepts the service-role key — server-side use only.

    Idempotency: a user GoTrue no longer knows yields
    ``already_absent=True`` — success, never an error.

    Error taxonomy (see :mod:`effaced_supabase.errors`): 4xx responses
    other than 404 and 429 raise
    :class:`~effaced.exceptions.ResolverError`; rate limits, 5xx, and
    connection faults propagate so the saga runner retries. A fresh
    ``httpx`` client is built per call — nothing loop- or
    connection-bound is cached on the instance (ADR 0006).
    """

    def __init__(
        self,
        base_url: str,
        service_role_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """Wire the resolver to a Supabase project.

        Args:
            base_url: The project's API origin, e.g.
                ``https://<project-ref>.supabase.co`` (or a self-hosted
                origin).
            service_role_key: The service-role key — the admin endpoints
                reject anon/publishable keys. Treat it as a root
                credential; never ship it client-side.
            transport: Optional transport override; tests inject a fake
                here so no call ever leaves the process.
            timeout: Per-request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {service_role_key}",
            "apikey": service_role_key,
        }
        self._transport = transport
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Stable resolver name recorded in manifests and audits."""
        return "supabase_auth"

    @property
    def covered_surface(self) -> CoveredSurface:
        """The GoTrue PII this resolver claims to reach (:class:`~effaced.AttestingResolver`).

        Returns:
            :data:`~effaced_supabase.auth_covered_surface.AUTH_COVERED_SURFACE`,
            built from the exporter's field tuple so it cannot drift.
        """
        return AUTH_COVERED_SURFACE

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Collect the user's GoTrue-held contact fields (Art. 15).

        Args:
            ref: ``kind="supabase_auth"``, ``value=<gotrue user id>``.

        Returns:
            The user's ``email`` and ``phone`` when populated (the field
            set lives in :mod:`effaced_supabase.auth_export_records`);
            empty when GoTrue holds no such user.

        Raises:
            ResolverError: The key is invalid, lacks admin access, or the
                request was malformed — retrying cannot succeed.
        """
        response = await asyncio.to_thread(self._request, "GET", ref.value)
        if response.status_code == _NOT_FOUND:
            return ResolverExport(resolver=self.name)
        raise_for_taxonomy(response, "export")
        return ResolverExport(resolver=self.name, records=user_records(response.json()))

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Hard-delete the user in GoTrue (Art. 17).

        Args:
            ref: ``kind="supabase_auth"``, ``value=<gotrue user id>``.

        Returns:
            The outcome; ``already_absent=True`` if GoTrue already had no
            such user.

        Raises:
            ResolverError: The key is invalid, lacks admin access, or the
                request was malformed — retrying cannot succeed.
        """
        response = await asyncio.to_thread(self._request, "DELETE", ref.value)
        if response.status_code == _NOT_FOUND:
            return ResolverErasure(
                resolver=self.name,
                already_absent=True,
                detail="user already absent in supabase auth",
            )
        raise_for_taxonomy(response, "erasure")
        return ResolverErasure(resolver=self.name, detail="user deleted in supabase auth")

    def _request(self, method: str, user_id: str) -> httpx.Response:
        """One admin-API call on a per-call client (runs in a worker thread).

        The id is percent-encoded into a single path segment — a raw
        ``/`` or ``..`` in a ref value would otherwise be path-normalized
        into a *different* user's endpoint (a wrong-target erasure).
        """
        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            return client.request(method, f"/auth/v1/admin/users/{quote(user_id, safe='')}")
