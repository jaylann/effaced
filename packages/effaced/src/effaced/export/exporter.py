"""The :class:`Exporter` — Art. 15 collection across database and resolvers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.export.bundle import ExportBundle

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectRef
    from effaced.manifest import DataMap
    from effaced.resolvers import ResolverRegistry


class Exporter:
    """Collects all of a subject's personal data into one structured bundle.

    Walks the data map for local data and fans out to registered resolvers
    for external systems. Resolver failures never silently shrink the
    bundle — they are recorded in ``incomplete_sources``.
    """

    def __init__(self, data_map: DataMap, registry: ResolverRegistry | None = None) -> None:
        """Wire the exporter to a manifest and optional resolver registry.

        Args:
            data_map: The application's data map.
            registry: Resolvers for external systems; ``None`` exports the
                local database only.
        """
        self._data_map = data_map
        self._registry = registry

    async def export_subject(
        self,
        session: Session,
        subject_id: str,
        *,
        refs: tuple[SubjectRef, ...] = (),
    ) -> ExportBundle:
        """Collect everything held on one subject (Art. 15).

        Args:
            session: An open database session; reads only, never writes.
            subject_id: Identifier on the subject table (see
                :class:`~effaced.annotations.SubjectLink`).
            refs: External-system references for resolver fan-out.

        Returns:
            The structured bundle including Art. 15 metadata (purposes,
            recipients, retention periods).
        """
        raise NotImplementedError
