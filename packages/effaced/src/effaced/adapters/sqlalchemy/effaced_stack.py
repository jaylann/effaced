"""The :class:`EffacedStack` — every engine wired from one declarative base."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from effaced.adapters.sqlalchemy.collector import collect_data_map
from effaced.adapters.sqlalchemy.erasure_executor import ErasureExecutor
from effaced.adapters.sqlalchemy.rectification_executor import RectificationExecutor
from effaced.adapters.sqlalchemy.resolution import resolve_subject_graph
from effaced.adapters.sqlalchemy.sql_status_counts_source import SqlStatusCountsSource
from effaced.adapters.sqlalchemy.storage.bind_tables import bind_tables
from effaced.audit.database_sink import DatabaseAuditSink
from effaced.consent.ledger import ConsentLedger
from effaced.erasure.planner import ErasurePlanner
from effaced.exceptions import ConfigurationError
from effaced.export.exporter import Exporter
from effaced.rectification.rectifier import Rectifier
from effaced.resolvers.registry import ResolverRegistry
from effaced.restriction.ledger import RestrictionLedger
from effaced.retention.sweeper import RetentionSweeper
from effaced.saga.outbox import Outbox
from effaced.saga.runner import SagaRunner

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import MetaData
    from sqlalchemy.orm import DeclarativeBase, sessionmaker

    from effaced.adapters.sqlalchemy.storage.effaced_tables import EffacedTables
    from effaced.audit.sink import AuditSink
    from effaced.manifest.data_map import DataMap
    from effaced.manifest.resolution.subject_graph import SubjectGraph
    from effaced.resolvers.base import Resolver


@dataclass(frozen=True, slots=True)
class EffacedStack:
    """Every effaced engine, wired once from an annotated declarative base.

    The manual integration sequence — collect the data map, resolve the
    subject graph, mount the owned tables, construct the audit sink, the
    outbox, and each engine — is mechanical and identical in every
    application. :meth:`from_base` performs it in one call and returns the
    wired components as named handles, so a web layer (or your own glue)
    only decides *when* to call them, never *how* to build them.

    The stack adds no behaviour of its own: each handle is exactly the
    component you could have constructed by hand, governed by its own
    documented contract. Construction executes no SQL — the owned tables
    ride your migrations (see :func:`effaced.bind_tables`).

    Attributes:
        metadata: The application ``MetaData`` the stack was built from.
        data_map: The manifest collected from the annotated models.
        graph: The resolved subject graph used to scope every operation.
        tables: Handles to the four effaced-owned tables.
        session_factory: The application's session factory, as provided.
        registry: The resolver registry routing external refs.
        audit_sink: The append-only trail every engine records into.
        outbox: The durable queue for external erasure/rectification calls.
        exporter: The Art. 15 export engine.
        planner: The Art. 17 erasure engine, execution-ready.
        rectifier: The Art. 16 rectification engine, execution-ready.
        consent: The Art. 7 consent ledger.
        restriction: The Art. 18 restriction-of-processing ledger.
        sweeper: The Art. 5(1)(e) retention sweeper (report-only).
        saga_runner: The outbox drainer — drive it from a worker, never
            on a serving event loop (ADR 0006).
    """

    metadata: MetaData
    data_map: DataMap
    graph: SubjectGraph
    tables: EffacedTables
    session_factory: sessionmaker  # type: ignore[type-arg]  # sessionmaker generic unbound here
    registry: ResolverRegistry
    audit_sink: AuditSink
    outbox: Outbox
    exporter: Exporter
    planner: ErasurePlanner
    rectifier: Rectifier
    consent: ConsentLedger
    restriction: RestrictionLedger
    sweeper: RetentionSweeper
    saga_runner: SagaRunner

    @classmethod
    def from_base(
        cls,
        base: type[DeclarativeBase],
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        *,
        resolvers: Sequence[Resolver] = (),
        registry: ResolverRegistry | None = None,
        audit_sink: AuditSink | None = None,
    ) -> EffacedStack:
        """Wire the full stack from an annotated declarative base.

        Collects the data map from ``base.metadata``, resolves the subject
        graph through ``base.registry``, mounts the owned tables, and
        constructs every engine with the SQLAlchemy executors. Resolver
        registration stays explicit (never discovered): pass the resolver
        instances, or a prebuilt registry — e.g. from
        :func:`effaced.registry_from_settings` — but not both.

        Args:
            base: The declarative base whose models carry the
                :func:`effaced.pii` / :func:`effaced.subject_link`
                annotations.
            session_factory: Factory producing sessions on the application
                database; used by the components that operate outside a
                caller transaction (audit sink, outbox claims).
            resolvers: External-system resolvers to register, by instance.
            registry: A prebuilt registry, mutually exclusive with
                ``resolvers``.
            audit_sink: Trail override; defaults to a
                :class:`effaced.DatabaseAuditSink` on the mounted
                ``effaced_audit_events`` table.

        Returns:
            The wired stack.

        Raises:
            ConfigurationError: If both ``resolvers`` and ``registry`` are
                given — two sources of truth for "where is my PII" would
                make the registration ambiguous.
            ManifestError: If the annotations on ``base`` are invalid
                (propagated from :func:`effaced.collect_data_map`).
        """
        if resolvers and registry is not None:
            msg = "pass either resolvers or a prebuilt registry, not both"
            raise ConfigurationError(msg)
        if registry is None:
            registry = ResolverRegistry()
            for resolver in resolvers:
                registry.register(resolver)
        data_map = collect_data_map(base.metadata)
        graph = resolve_subject_graph(data_map, base.registry)
        tables = bind_tables(base.metadata)
        audit = audit_sink or DatabaseAuditSink(session_factory, tables.audit_events)
        outbox = Outbox(
            session_factory,
            tables.outbox,
            status_counts_source=SqlStatusCountsSource(),
            audit_sink=audit,
        )
        return cls(
            metadata=base.metadata,
            data_map=data_map,
            graph=graph,
            tables=tables,
            session_factory=session_factory,
            registry=registry,
            audit_sink=audit,
            outbox=outbox,
            exporter=Exporter(data_map, graph, base.metadata, audit, registry),
            planner=ErasurePlanner(
                data_map,
                graph,
                registry,
                executor=ErasureExecutor(base.metadata),
                outbox=outbox,
                audit_sink=audit,
            ),
            rectifier=Rectifier(
                data_map,
                graph,
                registry,
                executor=RectificationExecutor(base.metadata),
                outbox=outbox,
                audit_sink=audit,
            ),
            consent=ConsentLedger(tables.consent_records, audit),
            restriction=RestrictionLedger(tables.restriction_records, audit),
            sweeper=RetentionSweeper(data_map, graph, base.metadata, audit),
            saga_runner=SagaRunner(registry, outbox, audit),
        )
