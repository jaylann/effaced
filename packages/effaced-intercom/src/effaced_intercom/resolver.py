"""The :class:`IntercomResolver` — the subject's Intercom contact and conversations."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from effaced.resolvers import ResolverErasure, ResolverExport
from effaced_intercom.covered_surface import INTERCOM_COVERED_SURFACE
from effaced_intercom.errors import raise_for_taxonomy
from effaced_intercom.export_records import contact_records, conversations_records

if TYPE_CHECKING:
    from effaced import CoveredSurface
    from effaced.annotations import SubjectRef

_NOT_FOUND = 404
_DEFAULT_BASE_URL = "https://api.intercom.io"
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_API_VERSION = "2.11"
_PER_PAGE = 150


class IntercomResolver:
    """Exports and erases a subject's Intercom contact and conversation metadata.

    Expects refs of kind ``"intercom"`` (refs are routed to the resolver
    whose name equals their kind — ADR 0008) whose value is the contact's
    **Intercom internal id**. Get and delete address the contact by id
    directly, so erasure needs no email lookup and no enumeration; the
    application resolves email-to-id in its own data map.

    Export (Art. 15) collects the contact's ``email``, ``name``, and
    ``phone``, plus per-conversation metadata — ``created_at``,
    ``updated_at``, and ``state`` for every conversation the contact
    appears in (paged through ``POST /conversations/search``). It never
    exports message bodies or the caller-defined ``custom_attributes``
    blob. Erasure (Art. 17) hard-deletes the contact via
    ``DELETE /contacts/{id}``.

    Idempotency: a contact Intercom no longer knows yields
    ``already_absent=True`` — success, never an error.

    Error taxonomy (see :mod:`effaced_intercom.errors`): 4xx responses
    other than 404 and 429 raise
    :class:`~effaced.exceptions.ResolverError`; rate limits, 5xx, and
    connection faults propagate so the saga runner retries. A fresh
    ``httpx`` client is built per call — nothing loop- or
    connection-bound is cached on the instance (ADR 0006).
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        api_version: str = _DEFAULT_API_VERSION,
    ) -> None:
        """Wire the resolver to an Intercom workspace.

        Args:
            access_token: An Intercom access token with read and delete
                access to contacts and read access to conversations.
                Treat it as a root credential; never ship it
                client-side.
            base_url: The API origin; override only when routing through
                a proxy or gateway (or Intercom's EU/AU regional hosts).
            transport: Optional transport override; tests inject a fake
                here so no call ever leaves the process.
            timeout: Per-request timeout in seconds.
            api_version: The ``Intercom-Version`` header value pinning the
                API surface the resolver was written against.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Intercom-Version": api_version,
        }
        self._transport = transport
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Stable resolver name recorded in manifests and audits."""
        return "intercom"

    @property
    def covered_surface(self) -> CoveredSurface:
        """The Intercom PII this resolver claims to reach (:class:`~effaced.AttestingResolver`).

        Returns:
            :data:`~effaced_intercom.covered_surface.INTERCOM_COVERED_SURFACE`,
            built from the exporter's field tuples so it cannot drift.
        """
        return INTERCOM_COVERED_SURFACE

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Collect the contact's profile and conversation metadata (Art. 15).

        Args:
            ref: ``kind="intercom"``, ``value=<Intercom contact id>``.

        Returns:
            The contact's ``email``/``name``/``phone`` and every
            conversation's ``created_at``/``updated_at``/``state`` (the
            field set lives in :mod:`effaced_intercom.export_records`);
            empty when Intercom holds no such contact.

        Raises:
            ResolverError: The token is invalid or lacks a scope, or the
                request was malformed — retrying cannot succeed.
        """
        contact = await asyncio.to_thread(self._get_contact, ref.value)
        if contact is None:
            return ResolverExport(resolver=self.name)
        conversations = await asyncio.to_thread(self._search_conversations, ref.value)
        records = (*contact_records(contact), *conversations_records(conversations))
        return ResolverExport(resolver=self.name, records=records)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Hard-delete the contact in Intercom (Art. 17).

        Args:
            ref: ``kind="intercom"``, ``value=<Intercom contact id>``.

        Returns:
            The outcome; ``already_absent=True`` if Intercom already had
            no such contact.

        Raises:
            ResolverError: The token is invalid or lacks a scope, or the
                request was malformed — retrying cannot succeed.
        """
        deleted = await asyncio.to_thread(self._delete_contact, ref.value)
        if not deleted:
            return ResolverErasure(
                resolver=self.name,
                already_absent=True,
                detail="contact already absent in intercom",
            )
        return ResolverErasure(resolver=self.name, detail="contact deleted in intercom")

    def _client(self) -> httpx.Client:
        """A fresh per-call client (nothing loop- or connection-bound is cached)."""
        return httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout,
            transport=self._transport,
        )

    def _get_contact(self, contact_id: str) -> Mapping[str, object] | None:
        """Retrieve the contact body, or ``None`` when Intercom holds no such id."""
        with self._client() as client:
            response = client.get(f"/contacts/{_segment(contact_id)}")
        if response.status_code == _NOT_FOUND:
            return None
        raise_for_taxonomy(response, "export")
        body = response.json()
        return body if isinstance(body, Mapping) else {}

    def _delete_contact(self, contact_id: str) -> bool:
        """Delete the contact; ``False`` means it was already absent (404)."""
        with self._client() as client:
            response = client.delete(f"/contacts/{_segment(contact_id)}")
        if response.status_code == _NOT_FOUND:
            return False
        raise_for_taxonomy(response, "erasure")
        return True

    def _search_conversations(self, contact_id: str) -> tuple[Mapping[str, object], ...]:
        """Page ``POST /conversations/search`` filtered to the contact, in one client."""
        conversations: list[Mapping[str, object]] = []
        starting_after: str | None = None
        with self._client() as client:
            while True:
                response = client.post(
                    "/conversations/search", json=_search_body(contact_id, starting_after)
                )
                raise_for_taxonomy(response, "export")
                payload = response.json()
                conversations.extend(_page_conversations(payload))
                starting_after = _next_cursor(payload)
                if starting_after is None:
                    return tuple(conversations)


def _segment(contact_id: str) -> str:
    """Percent-encode the contact id into a single path segment.

    A raw ``/`` or ``..`` in a ref value would otherwise be
    path-normalized into a *different* endpoint (a wrong-target erasure).
    Dots are unreserved, so a value of ``"."`` or ``".."`` survives
    :func:`~urllib.parse.quote` as a literal dot-segment; those are
    re-encoded as ``%2E``, which URL normalization leaves alone.
    """
    segment = quote(contact_id, safe="")
    if segment in (".", ".."):
        segment = segment.replace(".", "%2E")
    return segment


def _search_body(contact_id: str, starting_after: str | None) -> dict[str, object]:
    """Build the conversation-search request body for one page."""
    pagination: dict[str, object] = {"per_page": _PER_PAGE}
    if starting_after is not None:
        pagination["starting_after"] = starting_after
    return {
        "query": {"field": "contact_ids", "operator": "=", "value": contact_id},
        "pagination": pagination,
    }


def _page_conversations(payload: object) -> tuple[Mapping[str, object], ...]:
    """Extract the ``conversations`` array from one search payload."""
    if not isinstance(payload, Mapping):
        return ()
    conversations = payload.get("conversations")
    if not isinstance(conversations, list):
        return ()
    return tuple(item for item in conversations if isinstance(item, Mapping))


def _next_cursor(payload: object) -> str | None:
    """Dig ``pages.next.starting_after`` out of a search payload; ``None`` ends paging."""
    if not isinstance(payload, Mapping):
        return None
    pages = payload.get("pages")
    if not isinstance(pages, Mapping):
        return None
    nxt = pages.get("next")
    if not isinstance(nxt, Mapping):
        return None
    cursor = nxt.get("starting_after")
    return cursor if isinstance(cursor, str) and cursor else None
