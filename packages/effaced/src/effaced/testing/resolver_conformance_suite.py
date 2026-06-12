"""The :class:`ResolverConformanceSuite` — the resolver contract as tests.

Every resolver package subclasses this suite in its own tests and
implements the factory hooks; pytest then runs the inherited tests
against the real implementation (driven by fakes — never live calls).
The suite pins the three promises the saga runner and exporter rely on:
export shape, idempotent erasure, and the error taxonomy.
"""

from __future__ import annotations

import asyncio
from fnmatch import fnmatch
from typing import TYPE_CHECKING, TypeVar

import pytest

from effaced.exceptions import ResolverError
from effaced.resolvers import (
    AttestingResolver,
    RectifyingResolver,
    Resolver,
    ResolverErasure,
    ResolverExport,
    ResolverRectification,
    ResolverScheduledErasure,
    RetentionOnlyResolver,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from effaced.annotations import Correction, SubjectRef

_T = TypeVar("_T")


class ResolverConformanceSuite:
    """Inheritable contract tests for :class:`~effaced.resolvers.Resolver`.

    Subclass in your resolver package's tests with a ``Test``-prefixed
    name and implement :meth:`make_resolver`, :meth:`make_present_ref`,
    and :meth:`make_absent_ref`. Hooks are called fresh per test; state
    seeded by :meth:`make_resolver` must include the subject behind
    :meth:`make_present_ref` and never the one behind
    :meth:`make_absent_ref`.

    Override :meth:`make_nonretryable_resolver` and
    :meth:`make_transient_resolver` to also prove the error taxonomy;
    the corresponding tests skip while those hooks return ``None``.
    Resolvers implementing the optional
    :class:`~effaced.RectifyingResolver` capability override
    :meth:`make_corrections` to also prove the rectification contract —
    the rectify tests skip while the hook returns ``None`` or the
    resolver lacks ``rectify_subject``.

    Resolvers implementing :class:`~effaced.RetentionOnlyResolver`
    (ADR 0018) get the scheduled-erasure section instead of the on-demand
    erase tests, which skip — their ``erase_subject`` raises by contract.
    Override :meth:`make_expired_resolver` to also prove post-horizon
    verification; that test skips while the hook returns ``None``.

    Resolvers implementing the optional
    :class:`~effaced.AttestingResolver` capability get the covered-surface
    section: the present subject's export must stay within the declared
    surface (subset) and never touch a declared exclusion (absence).
    Override :meth:`make_fully_populated_resolver` to also prove the
    enumeration direction — every declared field is reachable; that test
    skips while the hook returns ``None``. The whole section skips for a
    resolver that does not attest a surface.
    """

    def make_resolver(self) -> Resolver:
        """Build the resolver under test, holding the present subject."""
        raise NotImplementedError

    def make_present_ref(self) -> SubjectRef:
        """Ref of a subject :meth:`make_resolver`'s system holds."""
        raise NotImplementedError

    def make_absent_ref(self) -> SubjectRef:
        """Ref of a subject the system never held."""
        raise NotImplementedError

    def make_nonretryable_resolver(self) -> Resolver | None:
        """Resolver wired to fail non-retryably (e.g. bad credentials)."""
        return None

    def make_transient_resolver(self) -> tuple[Resolver, type[Exception]] | None:
        """Resolver wired to fail transiently, plus the expected type."""
        return None

    def make_corrections(self) -> tuple[Correction, ...] | None:
        """Corrections for the rectification tests.

        The corrected values must differ from the state
        :meth:`make_resolver` seeds for the present subject — the suite
        proves the first application reports a change
        (``already_consistent=False``) and the second does not.
        """
        return None

    def make_expired_resolver(self) -> RetentionOnlyResolver | None:
        """A retention-only resolver whose present subject's horizon passed.

        Build the system so the subject behind :meth:`make_present_ref`
        was scheduled and its retention horizon already elapsed — the
        suite proves the next schedule verifies ``already_absent=True``
        and the export is empty.
        """
        return None

    def make_fully_populated_resolver(self) -> Resolver | None:
        """A resolver whose present subject populates every covered field.

        Build the system so the subject behind :meth:`make_present_ref`
        holds at least one value matching *every*
        :class:`~effaced.CoveredField` glob in the resolver's
        ``covered_surface`` — the maximal export. The suite then proves
        the enumeration direction: the declared surface contains no field
        the resolver can never emit. Skips while this returns ``None`` or
        the resolver does not attest a surface.
        """
        return None

    def _run(self, coro: Coroutine[None, None, _T]) -> _T:
        # The sanctioned sync->async bridge for this suite (ADR 0018):
        # pytest test methods are sync, so no loop is running here.
        return asyncio.run(coro)

    def test_satisfies_resolver_protocol(self) -> None:
        """The implementation satisfies the runtime-checkable protocol."""
        assert isinstance(self.make_resolver(), Resolver)

    def test_name_is_stable_and_matches_ref_kind(self) -> None:
        """Name is non-empty, instance-independent, and routes the refs."""
        name = self.make_resolver().name
        assert name
        assert self.make_resolver().name == name
        assert self.make_present_ref().kind == name
        assert self.make_absent_ref().kind == name

    def test_export_of_present_subject_yields_records(self) -> None:
        """Exporting a held subject returns its records under the name."""
        resolver = self.make_resolver()
        export = self._run(resolver.export_subject(self.make_present_ref()))
        assert isinstance(export, ResolverExport)
        assert export.resolver == resolver.name
        assert len(export.records) >= 1

    def test_export_of_absent_subject_is_empty(self) -> None:
        """Exporting an unknown subject is empty — never an error."""
        resolver = self.make_resolver()
        export = self._run(resolver.export_subject(self.make_absent_ref()))
        assert export.resolver == resolver.name
        assert export.records == ()

    def _erasing_resolver(self) -> Resolver:
        """The on-demand erase tests' resolver, or a skip (ADR 0018)."""
        resolver = self.make_resolver()
        if isinstance(resolver, RetentionOnlyResolver):
            pytest.skip("retention-only resolver: erasure is scheduled, never on demand")
        return resolver

    def test_erase_of_present_subject_succeeds(self) -> None:
        """Erasing a held subject succeeds with ``already_absent=False``."""
        resolver = self._erasing_resolver()
        erasure = self._run(resolver.erase_subject(self.make_present_ref()))
        assert isinstance(erasure, ResolverErasure)
        assert erasure.resolver == resolver.name
        assert erasure.already_absent is False

    def test_erase_is_idempotent(self) -> None:
        """A second erase of the same subject is success, already absent."""
        resolver = self._erasing_resolver()
        ref = self.make_present_ref()
        first = self._run(resolver.erase_subject(ref))
        second = self._run(resolver.erase_subject(ref))
        assert first.already_absent is False
        assert second.already_absent is True

    def test_erase_of_absent_subject_reports_already_absent(self) -> None:
        """Erasing a never-held subject is success — never an error."""
        erasure = self._run(self._erasing_resolver().erase_subject(self.make_absent_ref()))
        assert erasure.already_absent is True

    def test_export_after_erase_is_empty(self) -> None:
        """Erasure converges: a subsequent export holds nothing."""
        resolver = self._erasing_resolver()
        ref = self.make_present_ref()
        self._run(resolver.erase_subject(ref))
        export = self._run(resolver.export_subject(ref))
        assert export.records == ()

    def _erase_like(self, resolver: Resolver, ref: SubjectRef) -> object:
        """The resolver's erasure-shaped call — scheduled when retention-only."""
        if isinstance(resolver, RetentionOnlyResolver):
            return self._run(resolver.schedule_erasure(ref))
        return self._run(resolver.erase_subject(ref))

    def test_nonretryable_failure_raises_resolver_error(self) -> None:
        """Non-retryable faults surface as :class:`ResolverError`."""
        resolver = self.make_nonretryable_resolver()
        if resolver is None:
            pytest.skip("resolver package provides no non-retryable fault hook")
        ref = self.make_present_ref()
        with pytest.raises(ResolverError):
            self._run(resolver.export_subject(ref))
        with pytest.raises(ResolverError):
            self._erase_like(resolver, ref)

    def test_transient_failure_propagates_for_retry(self) -> None:
        """Transient faults propagate untranslated so the saga retries."""
        hook = self.make_transient_resolver()
        if hook is None:
            pytest.skip("resolver package provides no transient fault hook")
        resolver, expected = hook
        ref = self.make_present_ref()
        with pytest.raises(expected) as export_error:
            self._run(resolver.export_subject(ref))
        with pytest.raises(expected) as erase_error:
            self._erase_like(resolver, ref)
        assert not isinstance(export_error.value, ResolverError)
        assert not isinstance(erase_error.value, ResolverError)

    def _rectification_hook(self) -> tuple[RectifyingResolver, tuple[Correction, ...]]:
        """The rectify tests' resolver and corrections, or a skip."""
        corrections = self.make_corrections()
        if corrections is None:
            pytest.skip("resolver package provides no corrections hook")
        resolver = self.make_resolver()
        if not isinstance(resolver, RectifyingResolver):
            pytest.skip("resolver does not implement rectify_subject")
        return resolver, corrections

    def test_rectify_of_present_subject_succeeds(self) -> None:
        """Rectifying a held subject succeeds and reports a change."""
        resolver, corrections = self._rectification_hook()
        outcome = self._run(resolver.rectify_subject(self.make_present_ref(), corrections))
        assert isinstance(outcome, ResolverRectification)
        assert outcome.resolver == resolver.name
        assert outcome.already_consistent is False

    def test_rectify_is_idempotent(self) -> None:
        """Convergence: the second identical call is already consistent."""
        resolver, corrections = self._rectification_hook()
        ref = self.make_present_ref()
        first = self._run(resolver.rectify_subject(ref, corrections))
        second = self._run(resolver.rectify_subject(ref, corrections))
        assert first.already_consistent is False
        assert second.already_consistent is True

    def test_rectify_of_absent_subject_is_consistent_success(self) -> None:
        """Rectifying a never-held subject is success — never an error."""
        resolver, corrections = self._rectification_hook()
        outcome = self._run(resolver.rectify_subject(self.make_absent_ref(), corrections))
        assert outcome.already_consistent is True

    def _scheduling_resolver(self) -> RetentionOnlyResolver:
        """The scheduled-erasure tests' resolver, or a skip (ADR 0018)."""
        resolver = self.make_resolver()
        if not isinstance(resolver, RetentionOnlyResolver):
            pytest.skip("resolver does not implement schedule_erasure")
        return resolver

    def test_retention_only_erase_subject_raises(self) -> None:
        """On-demand erasure is refused — a schedule is never a deletion."""
        resolver = self._scheduling_resolver()
        with pytest.raises(ResolverError):
            self._run(resolver.erase_subject(self.make_present_ref()))

    def test_schedule_of_present_subject_reports_horizon(self) -> None:
        """Scheduling a held subject names a timezone-aware horizon."""
        resolver = self._scheduling_resolver()
        outcome = self._run(resolver.schedule_erasure(self.make_present_ref()))
        assert isinstance(outcome, ResolverScheduledErasure)
        assert outcome.resolver == resolver.name
        assert outcome.already_absent is False
        assert outcome.expires_at is not None

    def test_schedule_is_convergent(self) -> None:
        """Re-scheduling succeeds with the same-or-later horizon."""
        resolver = self._scheduling_resolver()
        ref = self.make_present_ref()
        first = self._run(resolver.schedule_erasure(ref))
        second = self._run(resolver.schedule_erasure(ref))
        assert second.already_absent is False
        assert first.expires_at is not None
        assert second.expires_at is not None
        assert second.expires_at >= first.expires_at

    def test_schedule_of_absent_subject_is_already_absent(self) -> None:
        """Scheduling a never-held subject is success — never an error."""
        resolver = self._scheduling_resolver()
        outcome = self._run(resolver.schedule_erasure(self.make_absent_ref()))
        assert outcome.already_absent is True
        assert outcome.expires_at is None

    def test_export_after_schedule_carries_expires_at(self) -> None:
        """Art. 15 honesty: a scheduled subject's records name a horizon."""
        resolver = self._scheduling_resolver()
        ref = self.make_present_ref()
        self._run(resolver.schedule_erasure(ref))
        export = self._run(resolver.export_subject(ref))
        assert len(export.records) >= 1
        assert all(record.expires_at is not None for record in export.records)

    def test_post_horizon_schedule_verifies_absent(self) -> None:
        """After the horizon, a schedule verifies the data is gone."""
        resolver = self.make_expired_resolver()
        if resolver is None:
            pytest.skip("resolver package provides no expired-horizon hook")
        ref = self.make_present_ref()
        outcome = self._run(resolver.schedule_erasure(ref))
        assert outcome.already_absent is True
        export = self._run(resolver.export_subject(ref))
        assert export.records == ()

    def _attesting_resolver(self) -> AttestingResolver:
        """The covered-surface tests' resolver, or a skip."""
        resolver = self.make_resolver()
        if not isinstance(resolver, AttestingResolver):
            pytest.skip("resolver does not implement covered_surface")
        return resolver

    def test_covered_surface_names_the_resolver(self) -> None:
        """The surface names this resolver and declares at least one field."""
        resolver = self._attesting_resolver()
        surface = resolver.covered_surface
        assert surface.resolver == resolver.name
        assert len(surface.fields) >= 1

    def test_export_stays_within_the_declared_surface(self) -> None:
        """Every exported field matches a covered glob of the same category."""
        resolver = self._attesting_resolver()
        surface = resolver.covered_surface
        export = self._run(resolver.export_subject(self.make_present_ref()))
        for record in export.records:
            assert any(
                fnmatch(record.field, covered.field) and record.category == covered.category
                for covered in surface.fields
            ), f"{record.field} ({record.category}) is outside the declared surface"

    def test_declared_exclusions_never_appear_in_exports(self) -> None:
        """No exported field matches a declared exclusion glob."""
        resolver = self._attesting_resolver()
        surface = resolver.covered_surface
        export = self._run(resolver.export_subject(self.make_present_ref()))
        for record in export.records:
            for exclusion in surface.exclusions:
                assert not fnmatch(
                    record.field, exclusion.field
                ), f"{record.field} matches excluded {exclusion.field}"

    def test_fully_populated_export_enumerates_the_declared_surface(self) -> None:
        """Every covered glob is matched by a record of the maximal export."""
        resolver = self.make_fully_populated_resolver()
        if resolver is None:
            pytest.skip("resolver package provides no fully-populated hook")
        if not isinstance(resolver, AttestingResolver):
            pytest.skip("resolver does not implement covered_surface")
        surface = resolver.covered_surface
        export = self._run(resolver.export_subject(self.make_present_ref()))
        for covered in surface.fields:
            assert any(
                fnmatch(record.field, covered.field) and record.category == covered.category
                for record in export.records
            ), f"declared {covered.field} ({covered.category}) is matched by no record"
