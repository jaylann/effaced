"""ResendResolver behaviour beyond the shared conformance suite.

Field mapping and categories, the never-exported ``properties`` blob,
wire-level auth and path-encoding, and the erase outcome details.
"""

from __future__ import annotations

import asyncio

from fake_resend_transport import FakeResendTransport

from effaced import PiiCategory, SubjectRef
from effaced_resend import ResendResolver
from effaced_resend.export_records import contact_records

KEY = "re_test_key"
EMAIL = "subject@example.com"


def test_non_object_contact_body_yields_no_records() -> None:
    assert contact_records(["not", "an", "object"]) == ()


def _resolver(fake: FakeResendTransport, **kwargs: object) -> ResendResolver:
    return ResendResolver(KEY, transport=fake, **kwargs)  # type: ignore[arg-type]


def _ref(value: str = EMAIL) -> SubjectRef:
    return SubjectRef(kind="resend", value=value)


def test_export_maps_every_held_field_to_its_category() -> None:
    fake = FakeResendTransport(
        contacts={
            EMAIL: {
                "id": "c-1",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "unsubscribed": True,
            }
        }
    )
    export = asyncio.run(_resolver(fake).export_subject(_ref()))
    by_field = {record.field: record for record in export.records}
    assert set(by_field) == {
        "contact.email",
        "contact.first_name",
        "contact.last_name",
        "contact.unsubscribed",
    }
    assert by_field["contact.email"].category is PiiCategory.CONTACT
    assert by_field["contact.email"].value == EMAIL
    assert by_field["contact.first_name"].category is PiiCategory.IDENTITY
    assert by_field["contact.first_name"].value == "Ada"
    assert by_field["contact.last_name"].category is PiiCategory.IDENTITY
    assert by_field["contact.last_name"].value == "Lovelace"
    assert by_field["contact.unsubscribed"].category is PiiCategory.BEHAVIORAL
    assert by_field["contact.unsubscribed"].value is True
    assert all(record.source == "resend" for record in export.records)


def test_empty_and_absent_name_fields_are_dropped() -> None:
    fake = FakeResendTransport(
        contacts={EMAIL: {"first_name": "", "unsubscribed": False}},
    )
    export = asyncio.run(_resolver(fake).export_subject(_ref()))
    fields = {record.field for record in export.records}
    assert fields == {"contact.email", "contact.unsubscribed"}


def test_properties_are_never_exported() -> None:
    fake = FakeResendTransport(
        contacts={EMAIL: {"properties": {"plan": "pro", "phone": "+4915112345678"}}},
    )
    export = asyncio.run(_resolver(fake).export_subject(_ref()))
    assert {record.field for record in export.records} == {"contact.email"}


def test_export_of_absent_contact_is_empty() -> None:
    export = asyncio.run(_resolver(FakeResendTransport()).export_subject(_ref()))
    assert export.records == ()


def test_requests_carry_the_bearer_key() -> None:
    fake = FakeResendTransport(contacts={EMAIL: {}})
    asyncio.run(_resolver(fake).export_subject(_ref()))
    asyncio.run(_resolver(fake).erase_subject(_ref()))
    assert all(req.headers["authorization"] == f"Bearer {KEY}" for req in fake.requests)


def test_ref_value_is_encoded_into_a_single_path_segment() -> None:
    hostile = "a/../b@example.com"
    fake = FakeResendTransport(contacts={hostile: {}})
    export = asyncio.run(_resolver(fake).export_subject(_ref(hostile)))
    assert len(export.records) == 1
    raw_path = fake.requests[0].url.raw_path.decode("ascii")
    assert raw_path == "/contacts/a%2F..%2Fb%40example.com"


def test_dot_only_ref_values_do_not_collapse_into_another_endpoint() -> None:
    for hostile in (".", ".."):
        fake = FakeResendTransport(contacts={hostile: {}})
        export = asyncio.run(_resolver(fake).export_subject(_ref(hostile)))
        assert len(export.records) == 1
        raw_path = fake.requests[0].url.raw_path.decode("ascii")
        assert raw_path == f"/contacts/{'%2E' * len(hostile)}"


def test_erase_reports_deletion_and_then_absence() -> None:
    fake = FakeResendTransport(contacts={EMAIL: {"id": "c-1"}})
    resolver = _resolver(fake)
    first = asyncio.run(resolver.erase_subject(_ref()))
    second = asyncio.run(resolver.erase_subject(_ref()))
    assert first.already_absent is False
    assert first.detail == "contact deleted in resend"
    assert second.already_absent is True
    assert second.detail == "contact already absent in resend"
    assert fake.deleted == {EMAIL}


def test_base_url_tolerates_a_trailing_slash() -> None:
    fake = FakeResendTransport(contacts={EMAIL: {}})
    resolver = ResendResolver(KEY, base_url="https://api.resend.com/", transport=fake)
    export = asyncio.run(resolver.export_subject(_ref()))
    assert len(export.records) == 1
    assert fake.requests[0].url.raw_path.decode("ascii").startswith("/contacts/")
