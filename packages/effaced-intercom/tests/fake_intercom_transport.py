"""In-memory Intercom API double, faked at the httpx transport boundary.

Faking at the transport layer exercises the resolver's real request
pipeline — URL construction, headers, JSON decoding, status handling, and
conversation-search pagination — without a call ever leaving the process.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from urllib.parse import unquote

import httpx

_CONTACT_PATH = re.compile(r"^/contacts/(?P<id>[^/]+)$")
_SEARCH_PATH = "/conversations/search"


class FakeIntercomTransport(httpx.MockTransport):
    """Stateful fake of Intercom's contact and conversation-search endpoints.

    Args:
        contacts: Seed contacts keyed by Intercom id; values are the JSON
            bodies ``GET /contacts/{id}`` should answer with.
        conversations: Seed conversations keyed by contact id; each value
            is the ordered list of conversation objects
            ``POST /conversations/search`` should page through for that
            contact.
        error_status: When set, every request answers with this status.
        connection_error: When True, every request raises
            ``httpx.ConnectError`` instead of answering.
        page_size: The maximum conversations returned per search page —
            set small to force multi-page pagination in tests.
    """

    def __init__(
        self,
        contacts: Mapping[str, Mapping[str, object]] | None = None,
        conversations: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
        *,
        error_status: int | None = None,
        connection_error: bool = False,
        page_size: int = 150,
    ) -> None:
        super().__init__(self._handle)
        self.contacts: dict[str, dict[str, object]] = {
            cid: dict(body) for cid, body in (contacts or {}).items()
        }
        self.conversations: dict[str, list[dict[str, object]]] = {
            cid: [dict(convo) for convo in convos] for cid, convos in (conversations or {}).items()
        }
        self.deleted: set[str] = set()
        self.requests: list[httpx.Request] = []
        self._error_status = error_status
        self._connection_error = connection_error
        self._page_size = page_size

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._connection_error:
            raise httpx.ConnectError("connection reset by fake transport", request=request)
        if self._error_status is not None:
            return _json_response(self._error_status, _error_body("injected_error"))
        # Route on the wire-level path: a real server sees %2F as one
        # segment, while request.url.path decodes it back to "/".
        raw_path = request.url.raw_path.decode("ascii")
        if request.method == "POST" and raw_path == _SEARCH_PATH:
            return self._search(request)
        match = _CONTACT_PATH.match(raw_path)
        if match is None:
            return _json_response(422, _error_body("not_found", "unknown route"))
        contact_id = unquote(match.group("id"))
        if request.method == "GET":
            return self._get_contact(contact_id)
        if request.method == "DELETE":
            return self._delete_contact(contact_id)
        return _json_response(405, _error_body("method_not_allowed"))

    def _get_contact(self, contact_id: str) -> httpx.Response:
        contact = self.contacts.get(contact_id)
        if contact is None:
            return _json_response(404, _NOT_FOUND_BODY)
        return _json_response(200, {"type": "contact", **contact})

    def _delete_contact(self, contact_id: str) -> httpx.Response:
        contact = self.contacts.pop(contact_id, None)
        if contact is None:
            return _json_response(404, _NOT_FOUND_BODY)
        self.deleted.add(contact_id)
        body = {"type": "contact", "id": contact.get("id", contact_id), "deleted": True}
        return _json_response(200, body)

    def _search(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        contact_id = body["query"]["value"]
        pagination = body.get("pagination") or {}
        per_page = min(int(pagination.get("per_page", self._page_size)), self._page_size)
        start = int(pagination.get("starting_after", 0))
        held = self.conversations.get(contact_id, [])
        page = held[start : start + per_page]
        next_index = start + per_page
        pages: dict[str, object] = {"type": "pages", "per_page": per_page}
        if next_index < len(held):
            pages["next"] = {"starting_after": str(next_index)}
        return _json_response(
            200,
            {
                "type": "conversation.list",
                "conversations": page,
                "total_count": len(held),
                "pages": pages,
            },
        )


_NOT_FOUND_BODY = {
    "type": "error.list",
    "errors": [{"code": "not_found", "message": "Contact Not Found"}],
}


def _error_body(code: str, message: str = "error") -> dict[str, object]:
    return {"type": "error.list", "errors": [{"code": code, "message": message}]}


def _json_response(status: int, body: Mapping[str, object]) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )
