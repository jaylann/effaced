"""The :class:`ResendResolver` — the subject's Resend contact record."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from effaced.resolvers import ResolverErasure, ResolverExport
from effaced_resend.errors import raise_for_taxonomy
from effaced_resend.export_records import contact_records

if TYPE_CHECKING:
    from effaced.annotations import SubjectRef

_NOT_FOUND = 404
_DEFAULT_BASE_URL = "https://api.resend.com"
_DEFAULT_TIMEOUT = 10.0


class ResendResolver:
    """Exports and erases a subject's Resend contact record.

    Expects refs of kind ``"resend"`` (refs are routed to the resolver
    whose name equals their kind — ADR 0008) whose value is the
    contact's email address as stored in Resend. Resend's
    global-contacts API addresses contacts by email directly, so one
    call reaches the contact across every segment — no enumeration, no
    audience configuration.

    Idempotency: a contact Resend no longer knows yields
    ``already_absent=True`` — success, never an error.

    Deleting the contact does not touch Resend's send history (which has
    no deletion API) and removes the contact's ``unsubscribed``
    preference with it — both are the controller's data-map concern, not
    this resolver's.

    Error taxonomy (see :mod:`effaced_resend.errors`): 4xx responses
    other than 404 and 429 raise
    :class:`~effaced.exceptions.ResolverError`; rate limits, 5xx, and
    connection faults propagate so the saga runner retries. A fresh
    ``httpx`` client is built per call — nothing loop- or
    connection-bound is cached on the instance (ADR 0006).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """Wire the resolver to a Resend team.

        Args:
            api_key: A Resend API key with full access (restricted
                sending-only keys cannot read or delete contacts).
                Treat it as a root credential; never ship it
                client-side.
            base_url: The API origin; override only when routing
                through a proxy or gateway.
            transport: Optional transport override; tests inject a fake
                here so no call ever leaves the process.
            timeout: Per-request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._transport = transport
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Stable resolver name recorded in manifests and audits."""
        return "resend"

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Collect the contact's Resend-held fields (Art. 15).

        Args:
            ref: ``kind="resend"``, ``value=<contact email>``.

        Returns:
            The contact's ``email``, name fields, and ``unsubscribed``
            flag when populated (the field set lives in
            :mod:`effaced_resend.export_records`); empty when Resend
            holds no such contact.

        Raises:
            ResolverError: The key is invalid or restricted, or the
                request was malformed — retrying cannot succeed.
        """
        response = await asyncio.to_thread(self._request, "GET", ref.value)
        if response.status_code == _NOT_FOUND:
            return ResolverExport(resolver=self.name)
        raise_for_taxonomy(response, "export")
        return ResolverExport(resolver=self.name, records=contact_records(response.json()))

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Hard-delete the contact in Resend (Art. 17).

        Args:
            ref: ``kind="resend"``, ``value=<contact email>``.

        Returns:
            The outcome; ``already_absent=True`` if Resend already had
            no such contact.

        Raises:
            ResolverError: The key is invalid or restricted, or the
                request was malformed — retrying cannot succeed.
        """
        response = await asyncio.to_thread(self._request, "DELETE", ref.value)
        if response.status_code == _NOT_FOUND:
            return ResolverErasure(
                resolver=self.name,
                already_absent=True,
                detail="contact already absent in resend",
            )
        raise_for_taxonomy(response, "erasure")
        return ResolverErasure(resolver=self.name, detail="contact deleted in resend")

    def _request(self, method: str, email: str) -> httpx.Response:
        """One contacts-API call on a per-call client (runs in a worker thread).

        The email is percent-encoded into a single path segment — a raw
        ``/`` or ``..`` in a ref value would otherwise be
        path-normalized into a *different* endpoint (a wrong-target
        erasure). Dots are unreserved, so a value of ``"."`` or ``".."``
        survives :func:`~urllib.parse.quote` as a literal dot-segment;
        those are re-encoded as ``%2E``, which URL normalization leaves
        alone.
        """
        segment = quote(email, safe="")
        if segment in (".", ".."):
            segment = segment.replace(".", "%2E")
        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            return client.request(method, f"/contacts/{segment}")
