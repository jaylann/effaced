"""In-memory GoTrue Admin API double, faked at the httpx transport boundary.

Faking at the transport layer exercises the resolver's real request
pipeline — URL construction, headers, JSON decoding, status handling —
without a call ever leaving the process.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

import httpx

_USERS_PATH = re.compile(r"^/auth/v1/admin/users/(?P<user_id>[^/]+)$")


class FakeGoTrueTransport(httpx.MockTransport):
    """Stateful fake of GoTrue's admin user endpoints.

    Args:
        users: Seed users keyed by GoTrue user id; values are the JSON
            bodies ``GET /auth/v1/admin/users/{id}`` should answer with.
        error_status: When set, every request answers with this status.
        connection_error: When True, every request raises
            ``httpx.ConnectError`` instead of answering.
    """

    def __init__(
        self,
        users: Mapping[str, Mapping[str, object]] | None = None,
        *,
        error_status: int | None = None,
        connection_error: bool = False,
    ) -> None:
        super().__init__(self._handle)
        self.users: dict[str, dict[str, object]] = {
            user_id: dict(user) for user_id, user in (users or {}).items()
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
            return _json_response(self._error_status, {"msg": "injected error"})
        match = _USERS_PATH.match(request.url.path)
        if match is None:
            return _json_response(400, {"msg": "unknown route"})
        user_id = match.group("user_id")
        if request.method == "GET":
            return self._get_user(user_id)
        if request.method == "DELETE":
            return self._delete_user(user_id)
        return _json_response(400, {"msg": "unsupported method"})

    def _get_user(self, user_id: str) -> httpx.Response:
        user = self.users.get(user_id)
        if user is None:
            return _json_response(404, {"msg": "User not found"})
        return _json_response(200, {"id": user_id, **user})

    def _delete_user(self, user_id: str) -> httpx.Response:
        if user_id not in self.users:
            return _json_response(404, {"msg": "User not found"})
        del self.users[user_id]
        self.deleted.add(user_id)
        return _json_response(200, {})


def _json_response(status: int, body: Mapping[str, object]) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )
