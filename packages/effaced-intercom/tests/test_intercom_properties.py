"""Properties: erasure hits exactly the target, and export pages exhaustively.

Contact ids are generated to include path-hostile characters (``/``,
``..``, ``%``, ``@``, unicode) so a path-normalization or substring bug in
URL construction would surface as a wrong-target erasure or a bleed.
Conversation counts and page sizes are generated so a pagination
off-by-one would surface as missing conversation records.
"""

from __future__ import annotations

import asyncio

import pytest
from fake_intercom_transport import FakeIntercomTransport
from hypothesis import given, settings
from hypothesis import strategies as st

from effaced import SubjectRef
from effaced_intercom import IntercomResolver

BEARER = "dG9rOnRlc3Q="

_ID_CHARS = "ab/.%@+_-? 漢"
_ids = st.text(alphabet=_ID_CHARS, min_size=1, max_size=12)


def _ref(value: str) -> SubjectRef:
    return SubjectRef(kind="intercom", value=value)


@pytest.mark.property
@settings(deadline=None)
@given(ids=st.lists(_ids, min_size=2, max_size=5, unique=True), data=st.data())
def test_erase_touches_exactly_the_target_contact(ids: list[str], data: st.DataObject) -> None:
    target = data.draw(st.sampled_from(ids), label="target")
    contacts = {cid: {"id": cid, "email": f"{i}@example.com"} for i, cid in enumerate(ids)}
    conversations = {cid: [{"id": f"{cid}-c", "state": "open"}] for cid in ids}
    fake = FakeIntercomTransport(contacts=contacts, conversations=conversations)
    resolver = IntercomResolver(BEARER, transport=fake)

    erasure = asyncio.run(resolver.erase_subject(_ref(target)))

    assert erasure.already_absent is False
    assert fake.deleted == {target}
    assert asyncio.run(resolver.export_subject(_ref(target))).records == ()
    for bystander in ids:
        if bystander == target:
            continue
        export = asyncio.run(resolver.export_subject(_ref(bystander)))
        fields = {record.field for record in export.records}
        assert "contact.email" in fields
        assert f"conversation.{bystander}-c.state" in fields


@pytest.mark.property
@settings(deadline=None)
@given(
    count=st.integers(min_value=0, max_value=20),
    page_size=st.integers(min_value=1, max_value=5),
)
def test_export_pages_collect_every_conversation(count: int, page_size: int) -> None:
    cid = "contact-1"
    convos = [
        {"id": f"conv-{i}", "created_at": 1_700_000_000 + i, "state": "closed"}
        for i in range(count)
    ]
    fake = FakeIntercomTransport(
        contacts={cid: {"id": cid, "email": "ada@example.com"}},
        conversations={cid: convos},
        page_size=page_size,
    )
    resolver = IntercomResolver(BEARER, transport=fake)

    export = asyncio.run(resolver.export_subject(_ref(cid)))

    created = {record.field for record in export.records if record.field.endswith(".created_at")}
    assert created == {f"conversation.conv-{i}.created_at" for i in range(count)}
