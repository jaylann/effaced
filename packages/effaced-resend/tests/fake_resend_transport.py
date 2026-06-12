"""In-memory Resend contacts API double, faked at the httpx transport boundary.

Faking at the transport layer exercises the resolver's real request
pipeline — URL construction, headers, JSON decoding, status handling —
without a call ever leaving the process.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import unquote

import httpx

_CONTACTS_PATH = re.compile(r"^/contacts/(?P<email>[^/]+)$")


class FakeResendTransport(httpx.MockTransport):
    """Stateful fake of Resend's contact endpoints.

    Args:
        contacts: Seed contacts keyed by email; values are the JSON
            bodies ``GET /contacts/{email}`` should answer with — they
            carry their own ``email`` key (or deliberately omit it),
            so tests prove the resolver reads the body, not the ref.
        error_status: When set, every request answers with this status.
        connection_error: When True, every request raises
            ``httpx.ConnectError`` instead of answering.
    """

    def __init__(
        self,
        contacts: Mapping[str, Mapping[str, object]] | None = None,
        *,
        error_status: int | None = None,
        connection_error: bool = False,
    ) -> None:
        super().__init__(self._handle)
        self.contacts: dict[str, dict[str, object]] = {
            email: dict(contact) for email, contact in (contacts or {}).items()
        }
        self.deleted: set[str] = set()
        self.requests: list[httpx.Request] = []
        self._error_status = error_status
        self._connection_error = connection_error

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._connection_error:
            raise httpx.ConnectError("connection reset by fake transport", request=request)
        if self._error_status is not None:
            return _json_response(self._error_status, {"name": "injected_error"})
        # Route on the wire-level path: a real server sees %2F as one
        # segment, while request.url.path decodes it back to "/".
        match = _CONTACTS_PATH.match(request.url.raw_path.decode("ascii"))
        if match is None:
            return _json_response(422, {"name": "validation_error", "message": "unknown route"})
        email = unquote(match.group("email"))
        if request.method == "GET":
            return self._get_contact(email)
        if request.method == "DELETE":
            return self._delete_contact(email)
        return _json_response(405, {"name": "method_not_allowed"})

    def _get_contact(self, email: str) -> httpx.Response:
        contact = self.contacts.get(email)
        if contact is None:
            return _json_response(404, _NOT_FOUND_BODY)
        return _json_response(200, {"object": "contact", **contact})

    def _delete_contact(self, email: str) -> httpx.Response:
        contact = self.contacts.pop(email, None)
        if contact is None:
            return _json_response(404, _NOT_FOUND_BODY)
        self.deleted.add(email)
        body = {"object": "contact", "contact": contact.get("id", ""), "deleted": True}
        return _json_response(200, body)


_NOT_FOUND_BODY = {"statusCode": 404, "name": "not_found", "message": "Contact not found"}


def _json_response(status: int, body: Mapping[str, object]) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )
