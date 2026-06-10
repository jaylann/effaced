"""A stateful in-process Stripe backend plugged in as `stripe.HTTPClient`.

Faking at the HTTP boundary exercises the real SDK pipeline: URL routing,
JSON parsing into typed objects, pagination, and the status→exception
mapping (404 → InvalidRequestError(code="resource_missing")) that the
resolver's error taxonomy depends on. No call ever leaves the process.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

from stripe import APIConnectionError, HTTPClient

_ERROR_BODIES: dict[int, dict[str, Any]] = {
    400: {"type": "invalid_request_error", "message": "Invalid request."},
    401: {"type": "invalid_request_error", "message": "Invalid API Key provided."},
    403: {
        "type": "invalid_request_error",
        "message": "This API key does not have the required permissions.",
    },
    429: {
        "type": "rate_limit_error",
        "code": "rate_limit",
        "message": "Too many requests.",
    },
    500: {"type": "api_error", "message": "Something went wrong on Stripe's end."},
}

_DEFAULT_PAGE_SIZE = 10


class FakeStripeHTTPClient(HTTPClient):
    """Routes the customer + payment-method endpoints against dict stores."""

    name: ClassVar[str] = "fake"

    def __init__(
        self,
        customers: Mapping[str, Mapping[str, Any]] | None = None,
        payment_methods: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        *,
        error_status: int | None = None,
        connection_error: bool = False,
        page_limit: int | None = None,
    ) -> None:
        super().__init__()
        self.page_limit = page_limit
        self.connection_error = connection_error
        self.customers: dict[str, dict[str, Any]] = {
            key: dict(value) for key, value in (customers or {}).items()
        }
        self.payment_methods: dict[str, list[dict[str, Any]]] = {
            key: [dict(item) for item in value] for key, value in (payment_methods or {}).items()
        }
        self.deleted: set[str] = set()
        self.error_status = error_status
        self.requests: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None,
        post_data: Any = None,
        *,
        _usage: list[str] | None = None,
    ) -> tuple[str, int, Mapping[str, str]]:
        method = method.lower()
        parsed = urlparse(url)
        self.requests.append((method, f"{parsed.path}?{parsed.query}"))
        if self.connection_error:
            raise APIConnectionError("connection reset by fake transport")
        if self.error_status is not None:
            return self._error(self.error_status)
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        parts = parsed.path.strip("/").split("/")
        if parts[:2] == ["v1", "customers"] and len(parts) == 3:
            if method == "get":
                return self._retrieve_customer(parts[2])
            if method == "delete":
                return self._delete_customer(parts[2])
        if (
            parts[:2] == ["v1", "customers"]
            and len(parts) == 4
            and parts[3] == "payment_methods"
            and method == "get"
        ):
            return self._list_payment_methods(parts[2], query)
        return self._error(400)

    def close(self) -> None:
        pass

    def _retrieve_customer(self, customer_id: str) -> tuple[str, int, Mapping[str, str]]:
        if customer_id in self.deleted:
            return self._deleted_stub(customer_id)
        customer = self.customers.get(customer_id)
        if customer is None:
            return self._missing(customer_id)
        body = {"id": customer_id, "object": "customer", **customer}
        return json.dumps(body), 200, {}

    def _delete_customer(self, customer_id: str) -> tuple[str, int, Mapping[str, str]]:
        if customer_id not in self.customers or customer_id in self.deleted:
            return self._missing(customer_id)
        del self.customers[customer_id]
        self.deleted.add(customer_id)
        return self._deleted_stub(customer_id)

    def _list_payment_methods(
        self, customer_id: str, query: Mapping[str, str]
    ) -> tuple[str, int, Mapping[str, str]]:
        if customer_id not in self.customers:
            return self._missing(customer_id)
        methods = self.payment_methods.get(customer_id, [])
        start = 0
        starting_after = query.get("starting_after")
        if starting_after is not None:
            ids = [method.get("id") for method in methods]
            start = ids.index(starting_after) + 1
        limit = int(query.get("limit", _DEFAULT_PAGE_SIZE))
        if self.page_limit is not None:
            limit = min(limit, self.page_limit)
        page = methods[start : start + limit]
        body = {
            "object": "list",
            "url": f"/v1/customers/{customer_id}/payment_methods",
            "data": [{"object": "payment_method", **method} for method in page],
            "has_more": start + limit < len(methods),
        }
        return json.dumps(body), 200, {}

    def _deleted_stub(self, customer_id: str) -> tuple[str, int, Mapping[str, str]]:
        body = {"id": customer_id, "object": "customer", "deleted": True}
        return json.dumps(body), 200, {}

    def _missing(self, customer_id: str) -> tuple[str, int, Mapping[str, str]]:
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "resource_missing",
                "param": "id",
                "message": f"No such customer: '{customer_id}'",
            }
        }
        return json.dumps(body), 404, {}

    def _error(self, status: int) -> tuple[str, int, Mapping[str, str]]:
        return json.dumps({"error": _ERROR_BODIES[status]}), status, {}
