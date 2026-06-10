"""The conformance suite verified against its reference InMemoryResolver."""

from __future__ import annotations

import pytest

from effaced import ExportRecord, PiiCategory, ResolverError, SubjectRef
from effaced.testing import InMemoryResolver, ResolverConformanceSuite

PRESENT = "subj-1"
ABSENT = "subj-never"

RECORDS = (ExportRecord(source="memory", field="email", category=PiiCategory.CONTACT),)


class TestInMemoryResolverConformance(ResolverConformanceSuite):
    """InMemoryResolver passes the full contract, fault hooks included."""

    def make_resolver(self) -> InMemoryResolver:
        return InMemoryResolver(records={PRESENT: RECORDS})

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="memory", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="memory", value=ABSENT)

    def make_nonretryable_resolver(self) -> InMemoryResolver:
        return InMemoryResolver(error=ResolverError("not wired"))

    def make_transient_resolver(self) -> tuple[InMemoryResolver, type[Exception]]:
        return InMemoryResolver(error=TimeoutError("slow")), TimeoutError


class _HooklessSuite(ResolverConformanceSuite):
    """Implements only the required hooks; fault hooks stay None."""

    def make_resolver(self) -> InMemoryResolver:
        return InMemoryResolver(records={PRESENT: RECORDS})

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="memory", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="memory", value=ABSENT)


def test_nonretryable_test_skips_without_hook():
    with pytest.raises(pytest.skip.Exception):
        _HooklessSuite().test_nonretryable_failure_raises_resolver_error()


def test_transient_test_skips_without_hook():
    with pytest.raises(pytest.skip.Exception):
        _HooklessSuite().test_transient_failure_propagates_for_retry()


def test_required_hooks_default_to_not_implemented():
    suite = ResolverConformanceSuite()
    for hook in (suite.make_resolver, suite.make_present_ref, suite.make_absent_ref):
        with pytest.raises(NotImplementedError):
            hook()
