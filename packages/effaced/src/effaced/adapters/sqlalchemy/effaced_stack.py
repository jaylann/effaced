"""The :class:`EffacedStack` — every engine wired from one schema source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from effaced.adapters.sqlalchemy.collector import collect_data_map
from effaced.adapters.sqlalchemy.erasure_executor import ErasureExecutor
from effaced.adapters.sqlalchemy.rectification_executor import RectificationExecutor
from effaced.adapters.sqlalchemy.reflection import reflect_metadata
from effaced.adapters.sqlalchemy.resolution import (
    resolve_subject_graph,
    resolve_subject_graph_from_fk,
)
from effaced.adapters.sqlalchemy.sql_status_counts_source import SqlStatusCountsSource
from effaced.adapters.sqlalchemy.storage.bind_tables import bind_tables
from effaced.audit.database_sink import DatabaseAuditSink
from effaced.consent.ledger import ConsentLedger
from effaced.erasure.planner import ErasurePlanner
from effaced.exceptions import ConfigurationError
from effaced.export.exporter import Exporter
from effaced.manifest.data_map import DataMap
from effaced.rectification.rectifier import Rectifier
from effaced.resolvers.registry import ResolverRegistry
from effaced.restriction.ledger import RestrictionLedger
from effaced.retention.sweeper import RetentionSweeper
from effaced.saga.outbox import Outbox
from effaced.saga.runner import SagaRunner

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Any

    from sqlalchemy import Engine, MetaData
    from sqlalchemy.orm import DeclarativeBase, sessionmaker

    from effaced.adapters.sqlalchemy.storage.effaced_tables import EffacedTables
    from effaced.audit.sink import AuditSink
    from effaced.manifest.resolution.subject_graph import SubjectGraph
    from effaced.resolvers.base import Resolver


class _ResolvedSchema(NamedTuple):
    """A metadata bundled with its collected manifest and resolved graph."""

    metadata: MetaData
    data_map: DataMap
    graph: SubjectGraph


def _resolve_registry(
    resolvers: Sequence[Resolver],
    registry: ResolverRegistry | None,
) -> ResolverRegistry:
    """Resolve the resolver registry shared by both entry points.

    Registration stays explicit (never discovered): accept resolver
    instances or a prebuilt registry, but not both.

    Raises:
        ConfigurationError: If both ``resolvers`` and ``registry`` are given —
            two sources of truth for "where is my PII" would make the
            registration ambiguous.
    """
    if resolvers and registry is not None:
        msg = "pass either resolvers or a prebuilt registry, not both"
        raise ConfigurationError(msg)
    if registry is not None:
        return registry
    built = ResolverRegistry()
    for resolver in resolvers:
        built.register(resolver)
    return built


@dataclass(frozen=True, slots=True)
class EffacedStack:
    """Every effaced engine, wired once from one schema source.

    The manual integration sequence — collect the data map, resolve the
    subject graph, mount the owned tables, construct the audit sink, the
    outbox, and each engine — is mechanical and identical in every
    application. The two classmethods perform it in one call and return the
    wired components as named handles, so a web layer (or your own glue)
    only decides *when* to call them, never *how* to build them.

    Pick the entry point by where the schema comes from. :meth:`from_base`
    derives the manifest and graph from an annotated declarative base — the
    in-process ORM is the source of truth. :meth:`from_manifest` runs the
    same engines from a serialized manifest paired with a reflected live
    database, for callers whose models live elsewhere (a separate service,
    another language) but who export the manifest and connect to the same
    database. Both wire identical engines and are proven byte-identical on
    the same schema.

    The stack adds no behaviour of its own: each handle is exactly the
    component you could have constructed by hand, governed by its own
    documented contract. Construction executes no SQL beyond the read-only
    reflection :meth:`from_manifest` performs — the owned tables ride your
    migrations (see :func:`effaced.bind_tables`).

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
        data_map = collect_data_map(base.metadata)
        graph = resolve_subject_graph(data_map, base.registry)
        schema = _ResolvedSchema(base.metadata, data_map, graph)
        return cls._wire(
            schema, session_factory, _resolve_registry(resolvers, registry), audit_sink
        )

    @classmethod
    def from_manifest(
        cls,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        engine: Engine,
        manifest_payload: Mapping[str, Any],
        *,
        resolvers: Sequence[Resolver] = (),
        registry: ResolverRegistry | None = None,
        audit_sink: AuditSink | None = None,
    ) -> EffacedStack:
        """Wire the full stack from a serialized manifest and a live database.

        The mapper-free counterpart of :meth:`from_base`, for callers whose
        models are not in this process. Loads (and forward-migrates) the
        manifest with :meth:`effaced.DataMap.from_payload`, reflects exactly
        the manifest's tables off ``engine`` with
        :func:`effaced.reflect_metadata`, resolves the subject graph from the
        reflected foreign keys with
        :func:`effaced.resolve_subject_graph_from_fk`, mounts the owned
        tables, and constructs every engine with the SQLAlchemy executors —
        the same engines :meth:`from_base` builds, proven byte-identical on
        the same schema. Subject-link paths in the manifest name **target
        tables**, not relationship attributes (the FK resolver's contract).

        Construction performs the read-only schema reflection and no other
        SQL: the owned tables ride your migrations (see
        :func:`effaced.bind_tables`), and reflection emits no DDL or DML.
        Resolver registration stays explicit (never discovered): pass the
        resolver instances, or a prebuilt registry, but not both.

        Args:
            session_factory: Factory producing sessions on the application
                database; used by the components that operate outside a
                caller transaction (audit sink, outbox claims). Bind it to
                the same database ``engine`` reflects.
            engine: A connectable engine on the live database to reflect the
                manifest's tables from.
            manifest_payload: A serialized manifest, as produced by
                :meth:`effaced.DataMap.to_payload` (any schema version —
                older payloads migrate forward).
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
                given.
            ManifestError: If the payload is structurally invalid or newer
                than this library understands (propagated from
                :meth:`effaced.DataMap.from_payload`).
            SubjectResolutionError: If the subject graph cannot be resolved
                from the reflected foreign keys (propagated from
                :func:`effaced.resolve_subject_graph_from_fk`).
        """
        data_map = DataMap.from_payload(dict(manifest_payload))
        metadata = reflect_metadata(engine, only=[entry.name for entry in data_map.tables])
        graph = resolve_subject_graph_from_fk(data_map, metadata)
        schema = _ResolvedSchema(metadata, data_map, graph)
        return cls._wire(
            schema, session_factory, _resolve_registry(resolvers, registry), audit_sink
        )

    @classmethod
    def _wire(
        cls,
        schema: _ResolvedSchema,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        registry: ResolverRegistry,
        audit_sink: AuditSink | None,
    ) -> EffacedStack:
        """Construct every engine from a resolved schema and registry.

        The shared tail of :meth:`from_base` and :meth:`from_manifest`: given
        a metadata with its collected manifest and resolved subject graph
        (built either from ORM mappers or reflected foreign keys) and the
        resolved resolver registry, it mounts the owned tables and wires the
        identical engines, so the entry point that produced the graph never
        affects how the engines behave.
        """
        metadata, data_map, graph = schema
        tables = bind_tables(metadata)
        audit = audit_sink or DatabaseAuditSink(session_factory, tables.audit_events)
        outbox = Outbox(
            session_factory,
            tables.outbox,
            status_counts_source=SqlStatusCountsSource(),
            audit_sink=audit,
        )
        return cls(
            metadata=metadata,
            data_map=data_map,
            graph=graph,
            tables=tables,
            session_factory=session_factory,
            registry=registry,
            audit_sink=audit,
            outbox=outbox,
            exporter=Exporter(data_map, graph, metadata, audit, registry),
            planner=ErasurePlanner(
                data_map,
                graph,
                registry,
                executor=ErasureExecutor(metadata),
                outbox=outbox,
                audit_sink=audit,
            ),
            rectifier=Rectifier(
                data_map,
                graph,
                registry,
                executor=RectificationExecutor(metadata),
                outbox=outbox,
                audit_sink=audit,
            ),
            consent=ConsentLedger(tables.consent_records, audit),
            restriction=RestrictionLedger(tables.restriction_records, audit),
            sweeper=RetentionSweeper(data_map, graph, metadata, audit),
            saga_runner=SagaRunner(registry, outbox, audit),
        )
