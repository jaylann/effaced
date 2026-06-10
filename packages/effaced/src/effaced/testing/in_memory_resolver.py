"""The :class:`InMemoryResolver` — reference fake for resolver testing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.resolvers.results import ResolverErasure, ResolverExport

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from effaced.annotations import SubjectRef
    from effaced.export import ExportRecord


class InMemoryResolver:
    """A dict-backed :class:`~effaced.resolvers.Resolver` for tests.

    Executable documentation of the resolver contract: erasure is
    idempotent (erasing an unknown subject reports ``already_absent=True``,
    never an error) and export converges to empty after erasure. Use it as
    a stand-in external system in application tests, and as the reference
    implementation the :class:`~effaced.testing.ResolverConformanceSuite`
    is itself verified against.

    Not safe for concurrent mutation — it is a test double, not a store.
    """

    def __init__(
        self,
        name: str = "memory",
        records: Mapping[str, Sequence[ExportRecord]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        """Seed the fake external system.

        Args:
            name: Resolver name; refs of this kind are routed here.
            records: Subject ref value -> the records the system holds.
            error: When set, both methods raise it — fault injection for
                testing error taxonomies.
        """
        self._name = name
        self._records: dict[str, tuple[ExportRecord, ...]] = {
            key: tuple(value) for key, value in (records or {}).items()
        }
        self._error = error

    @property
    def name(self) -> str:
        """Stable resolver name recorded in audits."""
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Return the seeded records for the subject; empty when unknown.

        Args:
            ref: ``kind=<name>``, ``value=<seeded key>``.

        Returns:
            The seeded records, or an empty export for unknown subjects.

        Raises:
            Exception: The injected ``error``, when one was configured.
        """
        if self._error is not None:
            raise self._error
        return ResolverExport(resolver=self._name, records=self._records.get(ref.value, ()))

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Drop the subject's records; already-gone is success.

        Args:
            ref: ``kind=<name>``, ``value=<seeded key>``.

        Returns:
            ``already_absent=True`` when the subject was not held.

        Raises:
            Exception: The injected ``error``, when one was configured.
        """
        if self._error is not None:
            raise self._error
        present = self._records.pop(ref.value, None) is not None
        return ResolverErasure(resolver=self._name, already_absent=not present)
