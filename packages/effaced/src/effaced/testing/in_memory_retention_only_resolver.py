"""The :class:`InMemoryRetentionOnlyResolver` — reference fake for scheduled erasure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from effaced.exceptions import ResolverError
from effaced.resolvers.export import ResolverExport
from effaced.resolvers.scheduled_erasure import ResolverScheduledErasure

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from effaced.annotations import SubjectRef
    from effaced.export import ExportRecord
    from effaced.resolvers.erasure import ResolverErasure


class InMemoryRetentionOnlyResolver:
    """A dict-backed :class:`~effaced.RetentionOnlyResolver` for tests (ADR 0018).

    Executable documentation of the scheduled-erasure contract:
    ``schedule_erasure`` reports a retention horizon instead of deleting
    (idempotent — re-scheduling keeps the first horizon), the data
    "expires" once the injected clock passes the horizon (exports turn
    empty and a later schedule verifies ``already_absent=True``), exports
    of a scheduled subject stamp ``expires_at`` on every record, and
    ``erase_subject`` raises :class:`~effaced.ResolverError` — a
    retention-only system cannot delete on demand, and a fabricated
    success would record a deletion that did not happen.

    Not safe for concurrent mutation — it is a test double, not a store.
    """

    def __init__(
        self,
        name: str = "retention_memory",
        records: Mapping[str, Sequence[ExportRecord]] | None = None,
        *,
        retention: timedelta = timedelta(days=30),
        clock: Callable[[], datetime] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Seed the fake retention-only external system.

        Args:
            name: Resolver name; refs of this kind are routed here.
            records: Subject ref value -> the records the system holds.
            retention: How far from "now" a fresh schedule's horizon lands.
            clock: Source of "now" (timezone-aware); defaults to real UTC
                time. Inject a controllable clock to step past horizons.
            error: When set, ``export_subject`` and ``schedule_erasure``
                raise it — fault injection for testing error taxonomies
                (``erase_subject`` raises ``ResolverError`` regardless, by
                contract).
        """
        self._name = name
        self._records: dict[str, tuple[ExportRecord, ...]] = {
            key: tuple(value) for key, value in (records or {}).items()
        }
        self._retention = retention
        self._clock = clock
        self._error = error
        self._horizons: dict[str, datetime] = {}

    @property
    def name(self) -> str:
        """Stable resolver name recorded in audits."""
        return self._name

    def _now(self) -> datetime:
        """The injected clock, or real UTC time."""
        return self._clock() if self._clock is not None else datetime.now(UTC)

    def _purge_expired(self) -> None:
        """Honour passed horizons — the fake vendor's retention job."""
        now = self._now()
        for value, horizon in list(self._horizons.items()):
            if horizon <= now:
                self._records.pop(value, None)
                del self._horizons[value]

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Return the held records, stamping the horizon once scheduled.

        Args:
            ref: ``kind=<name>``, ``value=<seeded key>``.

        Returns:
            The held records — each carrying ``expires_at`` when the
            subject's erasure is scheduled — or an empty export for
            unknown or already-expired subjects.

        Raises:
            Exception: The injected ``error``, when one was configured.
        """
        if self._error is not None:
            raise self._error
        self._purge_expired()
        held = self._records.get(ref.value, ())
        horizon = self._horizons.get(ref.value)
        if horizon is not None:
            held = tuple(record.model_copy(update={"expires_at": horizon}) for record in held)
        return ResolverExport(resolver=self._name, records=held)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:  # noqa: ARG002  # protocol signature; the refusal is unconditional
        """Refuse: a retention-only system cannot delete on demand.

        Args:
            ref: Ignored — the refusal is unconditional.

        Raises:
            ResolverError: Always; erasure is scheduled via
                :meth:`schedule_erasure` (ADR 0018).
        """
        msg = f"resolver {self._name!r} cannot delete on demand; use schedule_erasure"
        raise ResolverError(msg)

    async def schedule_erasure(self, ref: SubjectRef) -> ResolverScheduledErasure:
        """Schedule the subject's expiry; report the horizon or absence.

        Convergent: re-scheduling keeps the first horizon; a subject not
        held — never seeded, or already expired — is success with
        ``already_absent=True``.

        Args:
            ref: ``kind=<name>``, ``value=<seeded key>``.

        Returns:
            The horizon for a held subject, ``already_absent=True``
            otherwise.

        Raises:
            Exception: The injected ``error``, when one was configured.
        """
        if self._error is not None:
            raise self._error
        self._purge_expired()
        if ref.value not in self._records:
            return ResolverScheduledErasure(resolver=self._name, already_absent=True)
        horizon = self._horizons.setdefault(ref.value, self._now() + self._retention)
        return ResolverScheduledErasure(resolver=self._name, expires_at=horizon)
