"""Property: erasure touches exactly the target contact, never a bystander.

Emails are generated to include path-hostile characters (``/``, ``..``,
``%``, ``@``, unicode) so a path-normalization or substring bug in URL
construction would surface as a wrong-target erasure or a bleed.
"""

from __future__ import annotations

import asyncio

import pytest
from fake_resend_transport import FakeResendTransport
from hypothesis import given, settings
from hypothesis import strategies as st

from effaced import SubjectRef
from effaced_resend import ResendResolver

KEY = "re_test_key"

_EMAIL_CHARS = "ab/.%@+_-? 漢"
_emails = st.text(alphabet=_EMAIL_CHARS, min_size=1, max_size=20)


@pytest.mark.property
@settings(deadline=None)
@given(emails=st.lists(_emails, min_size=2, max_size=6, unique=True), data=st.data())
def test_erase_touches_exactly_the_target_contact(emails: list[str], data: st.DataObject) -> None:
    target = data.draw(st.sampled_from(emails), label="target")
    fake = FakeResendTransport(
        contacts={email: {"email": email, "first_name": "Ada"} for email in emails}
    )
    resolver = ResendResolver(KEY, transport=fake)

    erasure = asyncio.run(resolver.erase_subject(SubjectRef(kind="resend", value=target)))

    assert erasure.already_absent is False
    assert fake.deleted == {target}
    target_export = asyncio.run(resolver.export_subject(SubjectRef(kind="resend", value=target)))
    assert target_export.records == ()
    for bystander in emails:
        if bystander == target:
            continue
        ref = SubjectRef(kind="resend", value=bystander)
        export = asyncio.run(resolver.export_subject(ref))
        values = {record.value for record in export.records}
        assert bystander in values
