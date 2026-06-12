"""The conformance suite verified against its reference InMemoryResolver."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from effaced import Correction, ExportRecord, PiiCategory, ResolverError, SubjectRef
from effaced.testing import (
    InMemoryResolver,
    InMemoryRetentionOnlyResolver,
    ResolverConformanceSuite,
)

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

    def make_fully_populated_resolver(self) -> InMemoryResolver:
        return InMemoryResolver(records={PRESENT: RECORDS})


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


# --- covered-surface attestation (issue #123) ----------------------------------

ATTESTING_TESTS = (
    "test_covered_surface_names_the_resolver",
    "test_export_stays_within_the_declared_surface",
    "test_declared_exclusions_never_appear_in_exports",
    "test_fully_populated_export_enumerates_the_declared_surface",
)


class _AttestingSuite(_HooklessSuite):
    """The InMemoryResolver attests, so the covered-surface section runs."""

    def make_fully_populated_resolver(self) -> InMemoryResolver:
        return InMemoryResolver(records={PRESENT: RECORDS})


@pytest.mark.parametrize("test_name", ATTESTING_TESTS)
def test_attesting_tests_skip_for_a_non_attesting_resolver(test_name: str):
    """Capability absence is an honest answer — the suite skips, never fails."""
    with pytest.raises(pytest.skip.Exception):
        getattr(_NonRectifyingSuite(), test_name)()


def test_enumeration_test_skips_without_a_fully_populated_hook():
    """The enumeration direction skips while no maximal fixture is provided."""
    with pytest.raises(pytest.skip.Exception):
        _HooklessSuite().test_fully_populated_export_enumerates_the_declared_surface()


def test_attesting_suite_asserts_subset_exclusions_and_enumeration():
    """An attesting fake passes all three directions on real records."""
    suite = _AttestingSuite()
    suite.test_covered_surface_names_the_resolver()
    suite.test_export_stays_within_the_declared_surface()
    suite.test_declared_exclusions_never_appear_in_exports()
    suite.test_fully_populated_export_enumerates_the_declared_surface()


# --- retention-only reference fake (ADR 0018) ----------------------------------

RETENTION_RECORDS = (
    ExportRecord(source="retention_memory", field="recording", category=PiiCategory.COMMUNICATION),
)


class TestInMemoryRetentionOnlyResolverConformance(ResolverConformanceSuite):
    """The retention-only reference fake passes the scheduled-erasure contract."""

    def make_resolver(self) -> InMemoryRetentionOnlyResolver:
        return InMemoryRetentionOnlyResolver(records={PRESENT: RETENTION_RECORDS})

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="retention_memory", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="retention_memory", value=ABSENT)

    def make_nonretryable_resolver(self) -> InMemoryRetentionOnlyResolver:
        return InMemoryRetentionOnlyResolver(error=ResolverError("revoked key"))

    def make_transient_resolver(self) -> tuple[InMemoryRetentionOnlyResolver, type[Exception]]:
        return InMemoryRetentionOnlyResolver(error=TimeoutError("slow")), TimeoutError

    def make_expired_resolver(self) -> InMemoryRetentionOnlyResolver:
        """Schedule the present subject, then step the clock past its horizon."""
        now = [datetime(2026, 6, 1, 12, 0, tzinfo=UTC)]
        resolver = InMemoryRetentionOnlyResolver(
            records={PRESENT: RETENTION_RECORDS},
            retention=timedelta(days=30),
            clock=lambda: now[0],
        )
        scheduled = asyncio.run(resolver.schedule_erasure(self.make_present_ref()))
        assert scheduled.expires_at is not None
        now[0] = scheduled.expires_at + timedelta(seconds=1)
        return resolver


ERASE_TESTS = (
    "test_erase_of_present_subject_succeeds",
    "test_erase_is_idempotent",
    "test_erase_of_absent_subject_reports_already_absent",
    "test_export_after_erase_is_empty",
)

SCHEDULED_TESTS = (
    "test_retention_only_erase_subject_raises",
    "test_schedule_of_present_subject_reports_horizon",
    "test_schedule_is_convergent",
    "test_schedule_of_absent_subject_is_already_absent",
    "test_export_after_schedule_carries_expires_at",
    "test_post_horizon_schedule_verifies_absent",
)


@pytest.mark.parametrize("test_name", ERASE_TESTS)
def test_erase_tests_skip_for_a_retention_only_resolver(test_name: str) -> None:
    """On-demand erase proofs do not apply: erase_subject raises by contract."""
    with pytest.raises(pytest.skip.Exception):
        getattr(TestInMemoryRetentionOnlyResolverConformance(), test_name)()


@pytest.mark.parametrize("test_name", SCHEDULED_TESTS)
def test_scheduled_tests_skip_for_a_non_retention_only_resolver(test_name: str) -> None:
    """The scheduled section is isinstance-gated to RetentionOnlyResolver."""
    with pytest.raises(pytest.skip.Exception):
        getattr(_HooklessSuite(), test_name)()
