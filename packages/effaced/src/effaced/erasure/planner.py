"""The :class:`ErasurePlanner` — Art. 17 as a saga, not a function call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.erasure.plan import ErasurePlan
from effaced.erasure.result import ErasureResult

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectRef
    from effaced.manifest import DataMap
    from effaced.resolvers import ResolverRegistry


class ErasurePlanner:
    """Plans and executes subject erasure across local and external data.

    The local deletion runs in one atomic transaction in FK-safe order,
    honouring per-field strategies (delete / anonymize / retain). External
    calls cannot join that transaction, so they are enqueued durably in the
    same transaction and fanned out afterwards by the saga runner — the
    system is always in a known, recorded state, even on partial failure.
    """

    def __init__(self, data_map: DataMap, registry: ResolverRegistry | None = None) -> None:
        """Wire the planner to a manifest and optional resolver registry.

        Args:
            data_map: The application's data map.
            registry: Resolvers for external systems; ``None`` erases the
                local database only.
        """
        self._data_map = data_map
        self._registry = registry

    def plan(self, subject_id: str, *, refs: tuple[SubjectRef, ...] = ()) -> ErasurePlan:
        """Compute the erasure programme without executing anything.

        Args:
            subject_id: Identifier on the subject table.
            refs: External-system references for resolver steps.

        Returns:
            The ordered, inspectable plan (local steps first, FK-safe).

        Raises:
            RetentionViolationError: If the manifest demands deletion of a
                field that is simultaneously declared retained.
        """
        raise NotImplementedError

    async def erase_subject(
        self,
        session: Session,
        subject_id: str,
        *,
        refs: tuple[SubjectRef, ...] = (),
    ) -> ErasureResult:
        """Execute the plan: atomic local phase + durable external enqueue.

        Args:
            session: An open database session; the local phase commits or
                rolls back as one unit together with the outbox entries.
            subject_id: Identifier on the subject table.
            refs: External-system references for resolver steps.

        Returns:
            The local-phase outcome; external outcomes land in the audit
            trail asynchronously.
        """
        raise NotImplementedError
