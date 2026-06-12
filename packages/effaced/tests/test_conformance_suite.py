"""The conformance suite verified against its reference InMemoryResolver."""

from __future__ import annotations

import pytest

from effaced import Correction, ExportRecord, PiiCategory, ResolverError, SubjectRef
from effaced.testing import InMemoryResolver, ResolverConformanceSuite

PRESENT = "subj-1"
ABSENT = "subj-never"

RECORDS = (ExportRecord(source="memory", field="email", category=PiiCategory.CONTACT),)

CORRECTIONS = (Correction(category=PiiCategory.CONTACT, value="corrected@example.com"),)


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

    def make_corrections(self) -> tuple[Correction, ...]:
        return CORRECTIONS


class _HooklessSuite(ResolverConformanceSuite):
    """Implements only the required hooks; fault hooks stay None."""

    def make_resolver(self) -> InMemoryResolver:
        return InMemoryResolver(records={PRESENT: RECORDS})

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="memory", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="memory", value=ABSENT)


class _ErasureOnlyResolver:
    """A protocol-complete resolver deliberately lacking ``rectify_subject``."""

    def __init__(self) -> None:
        self._inner = InMemoryResolver(records={PRESENT: RECORDS})

    @property
    def name(self) -> str:
        return self._inner.name

    async def export_subject(self, ref: SubjectRef):
        return await self._inner.export_subject(ref)

    async def erase_subject(self, ref: SubjectRef):
        return await self._inner.erase_subject(ref)


class _NonRectifyingSuite(_HooklessSuite):
    """Provides corrections, but the resolver has no rectify capability."""

    def make_resolver(self) -> _ErasureOnlyResolver:  # type: ignore[override]
        return _ErasureOnlyResolver()

    def make_corrections(self) -> tuple[Correction, ...]:
        return CORRECTIONS


RECTIFY_TESTS = (
    "test_rectify_of_present_subject_succeeds",
    "test_rectify_is_idempotent",
    "test_rectify_of_absent_subject_is_consistent_success",
)


def test_nonretryable_test_skips_without_hook():
    with pytest.raises(pytest.skip.Exception):
        _HooklessSuite().test_nonretryable_failure_raises_resolver_error()


def test_transient_test_skips_without_hook():
    with pytest.raises(pytest.skip.Exception):
        _HooklessSuite().test_transient_failure_propagates_for_retry()


@pytest.mark.parametrize("test_name", RECTIFY_TESTS)
def test_rectify_tests_skip_without_corrections_hook(test_name: str):
    with pytest.raises(pytest.skip.Exception):
        getattr(_HooklessSuite(), test_name)()


@pytest.mark.parametrize("test_name", RECTIFY_TESTS)
def test_rectify_tests_skip_for_a_non_rectifying_resolver(test_name: str):
    """Capability absence is an honest answer — the suite skips, never fails."""
    with pytest.raises(pytest.skip.Exception):
        getattr(_NonRectifyingSuite(), test_name)()


def test_required_hooks_default_to_not_implemented():
    suite = ResolverConformanceSuite()
    for hook in (suite.make_resolver, suite.make_present_ref, suite.make_absent_ref):
        with pytest.raises(NotImplementedError):
            hook()
