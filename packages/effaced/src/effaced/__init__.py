"""effaced — GDPR data-subject mechanisms for your own stack.

Export (Art. 15), erasure (Art. 17), consent (Art. 7), and an append-only
audit trail (Art. 5(2)/30) across your own database *and* the external
systems you actually use. We ship the mechanisms. You own the compliance.

The integration surface is three calls: record consent, export a subject,
erase a subject. Everything else is bookkeeping the library does between
those calls.
"""

from importlib.metadata import PackageNotFoundError, version

from effaced.adapters.sqlalchemy import (
    EffacedTables,
    bind_tables,
    collect_data_map,
    pii,
    subject_link,
)
from effaced.annotations import PiiSpec, RetentionPolicy, SubjectLink, SubjectRef
from effaced.audit import AuditEvent, AuditEventType, AuditSink, DatabaseAuditSink
from effaced.categories import ErasureStrategy, LegalBasis, PiiCategory
from effaced.consent import ConsentLedger, ConsentRecord
from effaced.erasure import ErasurePlan, ErasurePlanner, ErasureResult, ErasureStep
from effaced.exceptions import (
    AuditIntegrityError,
    ConsentError,
    EffacedError,
    ManifestError,
    ResolverError,
    RetentionViolationError,
    SubjectResolutionError,
)
from effaced.export import ExportBundle, Exporter, ExportRecord
from effaced.manifest import MANIFEST_SCHEMA_VERSION, DataMap, TableEntry
from effaced.resolvers import Resolver, ResolverErasure, ResolverExport, ResolverRegistry
from effaced.saga import Outbox, OutboxEntry, OutboxStatus, SagaRunner

try:
    __version__ = version("effaced")
except PackageNotFoundError:  # pragma: no cover - only hit on uninstalled source trees
    __version__ = "0.0.0"

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "AuditEvent",
    "AuditEventType",
    "AuditIntegrityError",
    "AuditSink",
    "ConsentError",
    "ConsentLedger",
    "ConsentRecord",
    "DataMap",
    "DatabaseAuditSink",
    "EffacedError",
    "EffacedTables",
    "ErasurePlan",
    "ErasurePlanner",
    "ErasureResult",
    "ErasureStep",
    "ErasureStrategy",
    "ExportBundle",
    "ExportRecord",
    "Exporter",
    "LegalBasis",
    "ManifestError",
    "Outbox",
    "OutboxEntry",
    "OutboxStatus",
    "PiiCategory",
    "PiiSpec",
    "Resolver",
    "ResolverErasure",
    "ResolverError",
    "ResolverExport",
    "ResolverRegistry",
    "RetentionPolicy",
    "RetentionViolationError",
    "SagaRunner",
    "SubjectLink",
    "SubjectRef",
    "SubjectResolutionError",
    "TableEntry",
    "__version__",
    "bind_tables",
    "collect_data_map",
    "pii",
    "subject_link",
]
