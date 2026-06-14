"""IntercomResolver behaviour beyond the shared conformance suite.

Field mapping and categories, conversation-metadata mapping and
multi-page collection, the never-exported message bodies and
``custom_attributes`` blob, wire-level auth/version headers and
path-encoding, and the erase outcome details.
"""

from __future__ import annotations

import asyncio
import json

from fake_intercom_transport import FakeIntercomTransport

from effaced import PiiCategory, SubjectRef
from effaced_intercom import IntercomResolver
from effaced_intercom.export_records import contact_records, conversation_records

BEARER = "dG9rOnRlc3Q="
CID = "5f7f0d217ef88b001234abcd"


def _resolver(fake: FakeIntercomTransport, **kwargs: object) -> IntercomResolver:
    return IntercomResolver(BEARER, transport=fake, **kwargs)  # type: ignore[arg-type]


def _ref(value: str = CID) -> SubjectRef:
    return SubjectRef(kind="intercom", value=value)


def test_non_object_bodies_yield_no_records() -> None:
    assert contact_records(["not", "an", "object"]) == ()
    assert conversation_records(["not", "an", "object"]) == ()
    assert conversation_records({"created_at": 1, "state": "open"}) == ()  # no id


def test_export_maps_profile_and_conversation_metadata_to_categories() -> None:
    fake = FakeIntercomTransport(
        contacts={CID: {"id": CID, "email": "ada@example.com", "name": "Ada", "phone": "+44 1234"}},
        conversations={
            CID: [
                {
                    "id": "conv-1",
                    "created_at": 1700000000,
                    "updated_at": 1700000600,
                    "state": "closed",
                }
            ]
        },
    )
    export = asyncio.run(_resolver(fake).export_subject(_ref()))
    by_field = {record.field: record for record in export.records}
    assert set(by_field) == {
        "contact.email",
        "contact.name",
        "contact.phone",
        "conversation.conv-1.created_at",
        "conversation.conv-1.updated_at",
        "conversation.conv-1.state",
    }
    assert by_field["contact.email"].category is PiiCategory.CONTACT
    assert by_field["contact.email"].value == "ada@example.com"
    assert by_field["contact.name"].category is PiiCategory.IDENTITY
    assert by_field["contact.phone"].category is PiiCategory.CONTACT
    assert by_field["conversation.conv-1.created_at"].category is PiiCategory.BEHAVIORAL
    assert by_field["conversation.conv-1.created_at"].value == 1700000000
    assert by_field["conversation.conv-1.state"].value == "closed"
    assert all(record.source == "intercom" for record in export.records)


def test_empty_and_absent_profile_fields_are_dropped() -> None:
    fake = FakeIntercomTransport(
        contacts={CID: {"id": CID, "email": "ada@example.com", "name": "", "phone": None}},
    )
    export = asyncio.run(_resolver(fake).export_subject(_ref()))
    assert {record.field for record in export.records} == {"contact.email"}


def test_conversation_message_bodies_and_custom_attributes_never_exported() -> None:
    fake = FakeIntercomTransport(
        contacts={
            CID: {
                "id": CID,
                "email": "ada@example.com",
                "custom_attributes": {"plan": "pro", "ssn": "123-45-6789"},
            }
        },
        conversations={
            CID: [
                {
                    "id": "conv-1",
                    "state": "open",
                    "conversation_parts": {"conversation_parts": [{"body": "<p>secret</p>"}]},
                    "source": {"body": "<p>hello</p>"},
                }
            ]
        },
    )
    export = asyncio.run(_resolver(fake).export_subject(_ref()))
    fields = {record.field for record in export.records}
    assert fields == {"contact.email", "conversation.conv-1.state"}
    assert all("body" not in record.field for record in export.records)
    assert all("custom_attributes" not in record.field for record in export.records)


def test_export_pages_through_every_conversation() -> None:
    convos = [
        {"id": f"conv-{i}", "created_at": 1700000000 + i, "state": "closed"} for i in range(7)
    ]
    fake = FakeIntercomTransport(
        contacts={CID: {"id": CID, "email": "ada@example.com"}},
        conversations={CID: convos},
        page_size=2,  # force four pages (2+2+2+1)
    )
    export = asyncio.run(_resolver(fake).export_subject(_ref()))
    created = {record.field for record in export.records if record.field.endswith(".created_at")}
    assert created == {f"conversation.conv-{i}.created_at" for i in range(7)}
    # one contact GET + four search pages
    search_posts = [r for r in fake.requests if r.method == "POST"]
    assert len(search_posts) == 4


def test_export_of_absent_contact_is_empty_and_skips_conversation_search() -> None:
    fake = FakeIntercomTransport()
    export = asyncio.run(_resolver(fake).export_subject(_ref()))
    assert export.records == ()
    assert all(r.method != "POST" for r in fake.requests)


def test_requests_carry_bearer_token_and_version_header() -> None:
    fake = FakeIntercomTransport(
        contacts={CID: {"id": CID, "email": "ada@example.com"}}, conversations={CID: []}
    )
    asyncio.run(_resolver(fake).export_subject(_ref()))
    asyncio.run(_resolver(fake).erase_subject(_ref()))
    assert all(req.headers["authorization"] == f"Bearer {BEARER}" for req in fake.requests)
    assert all(req.headers["intercom-version"] == "2.11" for req in fake.requests)


def test_search_query_filters_by_contact_id() -> None:
    fake = FakeIntercomTransport(
        contacts={CID: {"id": CID, "email": "ada@example.com"}}, conversations={CID: []}
    )
    asyncio.run(_resolver(fake).export_subject(_ref()))
    post = next(r for r in fake.requests if r.method == "POST")
    body = json.loads(post.content)
    assert body["query"] == {"field": "contact_ids", "operator": "=", "value": CID}


def test_ref_value_is_encoded_into_a_single_path_segment() -> None:
    hostile = "a/../b"
    fake = FakeIntercomTransport(contacts={hostile: {"id": hostile, "email": "x@example.com"}})
    export = asyncio.run(_resolver(fake).export_subject(_ref(hostile)))
    assert len(export.records) == 1
    raw_path = fake.requests[0].url.raw_path.decode("ascii")
    assert raw_path == "/contacts/a%2F..%2Fb"


def test_dot_only_ref_values_do_not_collapse_into_another_endpoint() -> None:
    for hostile in (".", ".."):
        fake = FakeIntercomTransport(contacts={hostile: {"id": hostile, "email": "x@example.com"}})
        export = asyncio.run(_resolver(fake).export_subject(_ref(hostile)))
        assert len(export.records) == 1
        raw_path = fake.requests[0].url.raw_path.decode("ascii")
        assert raw_path == f"/contacts/{'%2E' * len(hostile)}"


def test_erase_reports_deletion_and_then_absence() -> None:
    fake = FakeIntercomTransport(contacts={CID: {"id": CID}})
    resolver = _resolver(fake)
    first = asyncio.run(resolver.erase_subject(_ref()))
    second = asyncio.run(resolver.erase_subject(_ref()))
    assert first.already_absent is False
    assert first.detail == "contact deleted in intercom"
    assert second.already_absent is True
    assert second.detail == "contact already absent in intercom"
    assert fake.deleted == {CID}


def test_base_url_tolerates_a_trailing_slash() -> None:
    fake = FakeIntercomTransport(
        contacts={CID: {"id": CID, "email": "ada@example.com"}}, conversations={CID: []}
    )
    resolver = IntercomResolver(BEARER, base_url="https://api.intercom.io/", transport=fake)
    export = asyncio.run(resolver.export_subject(_ref()))
    assert len(export.records) == 1
    assert fake.requests[0].url.raw_path.decode("ascii").startswith("/contacts/")
