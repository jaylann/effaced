"""The :class:`ResolverConformanceSuite` — the resolver contract as tests.

Every resolver package subclasses this suite in its own tests and
implements the factory hooks; pytest then runs the inherited tests
against the real implementation (driven by fakes — never live calls).
The suite pins the three promises the saga runner and exporter rely on:
export shape, idempotent erasure, and the error taxonomy.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

import pytest

from effaced.exceptions import ResolverError
from effaced.resolvers import Resolver, ResolverErasure, ResolverExport

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from effaced.annotations import SubjectRef

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

    def _run(self, coro: Coroutine[None, None, _T]) -> _T:
        # The sanctioned sync->async bridge for this suite (ADR 0011):
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

    def test_erase_of_present_subject_succeeds(self) -> None:
        """Erasing a held subject succeeds with ``already_absent=False``."""
        resolver = self.make_resolver()
        erasure = self._run(resolver.erase_subject(self.make_present_ref()))
        assert isinstance(erasure, ResolverErasure)
        assert erasure.resolver == resolver.name
        assert erasure.already_absent is False

    def test_erase_is_idempotent(self) -> None:
        """A second erase of the same subject is success, already absent."""
        resolver = self.make_resolver()
        ref = self.make_present_ref()
        first = self._run(resolver.erase_subject(ref))
        second = self._run(resolver.erase_subject(ref))
        assert first.already_absent is False
        assert second.already_absent is True

    def test_erase_of_absent_subject_reports_already_absent(self) -> None:
        """Erasing a never-held subject is success — never an error."""
        erasure = self._run(self.make_resolver().erase_subject(self.make_absent_ref()))
        assert erasure.already_absent is True

    def test_export_after_erase_is_empty(self) -> None:
        """Erasure converges: a subsequent export holds nothing."""
        resolver = self.make_resolver()
        ref = self.make_present_ref()
        self._run(resolver.erase_subject(ref))
        export = self._run(resolver.export_subject(ref))
        assert export.records == ()

    def test_nonretryable_failure_raises_resolver_error(self) -> None:
        """Non-retryable faults surface as :class:`ResolverError`."""
        resolver = self.make_nonretryable_resolver()
        if resolver is None:
            pytest.skip("resolver package provides no non-retryable fault hook")
        ref = self.make_present_ref()
        with pytest.raises(ResolverError):
            self._run(resolver.export_subject(ref))
        with pytest.raises(ResolverError):
            self._run(resolver.erase_subject(ref))

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
            self._run(resolver.erase_subject(ref))
        assert not isinstance(export_error.value, ResolverError)
        assert not isinstance(erase_error.value, ResolverError)
