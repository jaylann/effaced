"""The :class:`DjangoEffacedStack` — every engine wired from Django models.

The Django sibling of :class:`effaced.EffacedStack`. It translates annotated
Django models into SQLAlchemy metadata (:mod:`effaced_django.introspection`),
resolves the subject graph from foreign keys
(:func:`effaced.resolve_subject_graph_from_fk`), and wires the **unchanged**
core engines on the resulting metadata. Erasure, export, rectification,
consent, restriction, retention, and the saga therefore behave identically to
the SQLAlchemy stack — the only difference is where the schema came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from effaced.adapters.sqlalchemy.collector import collect_data_map
from effaced.adapters.sqlalchemy.erasure_executor import ErasureExecutor
from effaced.adapters.sqlalchemy.rectification_executor import RectificationExecutor
from effaced.adapters.sqlalchemy.resolution import resolve_subject_graph_from_fk
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
from effaced_django.introspection import build_metadata
from effaced_django.registry import default_registry

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy import MetaData
    from sqlalchemy.orm import sessionmaker

    from effaced.adapters.sqlalchemy.storage.effaced_tables import EffacedTables
    from effaced.audit.sink import AuditSink
    from effaced.manifest.data_map import DataMap
    from effaced.manifest.resolution.subject_graph import SubjectGraph
    from effaced.resolvers.base import Resolver
    from effaced_django.registry import ModelAnnotation


@dataclass(frozen=True, slots=True)
class DjangoEffacedStack:
    """Every effaced engine, wired once from annotated Django models.

    The Django counterpart of :class:`effaced.EffacedStack`: the same named
    handles, governed by the same contracts, built by :meth:`from_models`.
    Construction executes no SQL — the owned tables are mounted onto the
    derived ``MetaData`` (use ``metadata.create_all`` or a caller migration
    to materialize them; native Django migrations for the owned tables are a
    planned follow-up).

    Attributes:
        metadata: The SQLAlchemy ``MetaData`` derived from the Django models.
        data_map: The manifest collected from the models.
        graph: The subject graph resolved from foreign-key constraints.
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
        saga_runner: The outbox drainer — drive it from a worker, never on a
            serving event loop (ADR 0006).
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
    def from_models(
        cls,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        *,
        annotations: Iterable[ModelAnnotation] | None = None,
        resolvers: Sequence[Resolver] = (),
        registry: ResolverRegistry | None = None,
        audit_sink: AuditSink | None = None,
    ) -> DjangoEffacedStack:
        """Wire the full stack from annotated Django models.

        Translates the models to metadata, collects the data map, resolves
        the subject graph from foreign keys, mounts the owned tables, and
        constructs every engine with the SQLAlchemy executors. Resolver
        registration stays explicit (never discovered).

        Args:
            session_factory: Factory producing sessions on the application
                database, used by the components that operate outside a
                caller transaction (audit sink, outbox claims). Bind it to
                the same database Django uses, and run effaced inside a
                ``transaction.atomic()`` block so the outbox enqueue shares
                the transaction with the local erasure (ADR 0010).
            annotations: Model annotations to wire; defaults to the
                process-wide :data:`effaced_django.default_registry`.
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
            EffacedDjangoError: If a model field cannot be translated.
            ManifestError: If the annotations are invalid.
            SubjectResolutionError: If the subject graph cannot be resolved.
        """
        if resolvers and registry is not None:
            msg = "pass either resolvers or a prebuilt registry, not both"
            raise ConfigurationError(msg)
        if registry is None:
            registry = ResolverRegistry()
            for resolver in resolvers:
                registry.register(resolver)
        resolved = tuple(annotations) if annotations is not None else default_registry.annotations
        metadata = build_metadata(resolved)
        data_map = collect_data_map(metadata)
        graph = resolve_subject_graph_from_fk(data_map, metadata)
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
