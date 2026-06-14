"""In-memory PostgREST data API double, faked at the httpx transport boundary.

Faking at the transport layer exercises the resolver's real request
pipeline — URL construction, query encoding, headers, JSON decoding,
status handling — without a call ever leaving the process. The store is a
table name to list-of-rows mapping; the fake answers the single
``{subject_column}=eq.{id}`` horizontal filter the resolver issues.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from urllib.parse import unquote

import httpx

_TABLE_PATH = re.compile(r"^/rest/v1/(?P<table>[^/?]+)")

Row = Mapping[str, object]


class FakePostgrestTransport(httpx.MockTransport):
    """Stateful fake of a PostgREST table API.

    Args:
        tables: Seed rows keyed by table name; each value is the list of
            row objects that table holds.
        error_status: When set, every request answers with this status.
        connection_error: When True, every request raises
            ``httpx.ConnectError`` instead of answering.
    """

    def __init__(
        self,
        tables: Mapping[str, Sequence[Row]] | None = None,
        *,
        error_status: int | None = None,
        connection_error: bool = False,
    ) -> None:
        super().__init__(self._handle)
        self.tables: dict[str, list[dict[str, object]]] = {
            name: [dict(row) for row in rows] for name, rows in (tables or {}).items()
        }
        self.deleted: list[tuple[str, str]] = []
        self.requests: list[httpx.Request] = []
        self._error_status = error_status
        self._connection_error = connection_error

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._connection_error:
            raise httpx.ConnectError("connection reset by fake transport", request=request)
        if self._error_status is not None:
            return _json_response(self._error_status, {"message": "injected error"})
        match = _TABLE_PATH.match(request.url.raw_path.decode("ascii"))
        if match is None:
            return _json_response(400, {"message": "unknown route"})
        table = unquote(match.group("table"))
        if table not in self.tables:
            return _json_response(404, {"message": f"relation {table} does not exist"})
        column, value = _subject_filter(request)
        if column is None or value is None:
            return _json_response(400, {"message": "missing eq filter"})
        return self._dispatch(table, column, value, request)

    def _dispatch(
        self, table: str, column: str, value: str, request: httpx.Request
    ) -> httpx.Response:
        if request.method == "GET":
            return self._select(table, column, value, request)
        if request.method == "DELETE":
            return self._delete(table, column, value)
        return _json_response(405, {"message": "unsupported method"})

    def _select(
        self, table: str, column: str, value: str, request: httpx.Request
    ) -> httpx.Response:
        selected = _selected_columns(request)
        rows = [
            _project(row, selected) for row in self.tables[table] if _matches(row, column, value)
        ]
        return _json_response(200, rows)

    def _delete(self, table: str, column: str, value: str) -> httpx.Response:
        kept: list[dict[str, object]] = []
        removed: list[dict[str, object]] = []
        for row in self.tables[table]:
            (removed if _matches(row, column, value) else kept).append(row)
        self.tables[table] = kept
        if removed:
            self.deleted.append((table, value))
        return _json_response(200, removed)


def _subject_filter(request: httpx.Request) -> tuple[str | None, str | None]:
    """The ``{column}=eq.{value}`` filter param, ignoring ``select``."""
    for key, raw in request.url.params.multi_items():
        if key == "select":
            continue
        if raw.startswith("eq."):
            return key, raw[len("eq.") :]
    return None, None


def _selected_columns(request: httpx.Request) -> tuple[str, ...] | None:
    select = request.url.params.get("select")
    if not select:
        return None
    return tuple(select.split(","))


def _project(row: Mapping[str, object], columns: tuple[str, ...] | None) -> dict[str, object]:
    if columns is None:
        return dict(row)
    return {column: row[column] for column in columns if column in row}


def _matches(row: Mapping[str, object], column: str, value: str) -> bool:
    """Literal ``eq`` match — the stored value stringified equals the filter."""
    return column in row and str(row[column]) == value


def _json_response(status: int, body: object) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )
