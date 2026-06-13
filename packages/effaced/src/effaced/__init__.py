"""effaced — GDPR data-subject mechanisms for your own stack.

Export (Art. 15), erasure (Art. 17), rectification (Art. 16), consent
(Art. 7), restriction of processing (Art. 18), and an append-only audit
trail (Art. 5(2)/30) across your own database *and* the external systems
you actually use. We ship the mechanisms. You own the compliance.

The integration surface is a handful of calls: record consent, record a
restriction, export a subject, erase a subject, rectify a subject.
Everything else is bookkeeping the library does between those calls.
"""

from importlib.metadata import PackageNotFoundError, version

from effaced.adapters.sqlalchemy import (
    EffacedStack,
    EffacedTables,
    ErasureExecutor,
    ErasureVerifier,
    LintTarget,
    RectificationExecutor,
    SqlStatusCountsSource,
    SurrogateRegistry,
    bind_tables,
    collect_data_map,
    default_surrogate_registry,
    lint_completeness,
    lint_reachability,
    load_lint_target,
    pii,
    resolve_subject_graph,
    subject_link,
)
from effaced.annotations import Correction, PiiSpec, RetentionPolicy, SubjectLink, SubjectRef
from effaced.audit import AuditEvent, AuditEventType, AuditSink, DatabaseAuditSink
from effaced.categories import ErasureStrategy, LegalBasis, PiiCategory
from effaced.consent import ConsentLedger, ConsentRecord
from effaced.erasure import (
    ErasurePlan,
    ErasurePlanner,
    ErasureResult,
    ErasureStep,
    ErasureVerification,
    StepExecutor,
)
from effaced.exceptions import (
    AnonymizationError,
    AuditIntegrityError,
    ConfigurationError,
    ConsentError,
    EffacedError,
    ManifestError,
    ResolverError,
    RetentionViolationError,
    SubjectResolutionError,
)
from effaced.export import ExportBundle, Exporter, ExportRecord
from effaced.lint import CompletenessFinding, ReachabilityFinding
from effaced.manifest import (
    MANIFEST_SCHEMA_VERSION,
    ColumnEntry,
    DataMap,
    JoinHop,
    SubjectGraph,
    TableAccessPlan,
    TableEntry,
    fk_safe_deletion_order,
)
from effaced.rectification import (
    RectificationResult,
    RectificationStep,
    RectificationStepExecutor,
    Rectifier,
)
from effaced.replay import Replayer, ReplayPlan, ReplayPlanEntry, ReplaySource
from effaced.resolvers import (
    AttestingResolver,
    CoveredField,
    CoveredSurface,
    RectifyingResolver,
    RegistryBuild,
    Resolver,
    ResolverErasure,
    ResolverExport,
    ResolverRectification,
    ResolverRegistry,
    ResolverScheduledErasure,
    ResolverSpec,
    RetentionOnlyResolver,
    SpecOutcome,
    SurfaceExclusion,
    registry_from_settings,
)
from effaced.restriction import RestrictionLedger, RestrictionRecord
from effaced.retention import RetentionReport, RetentionReportEntry, RetentionSweeper
from effaced.saga import (
    AbandonedHook,
    AbandonedSignal,
    BackoffPolicy,
    Outbox,
    OutboxEntry,
    OutboxOperation,
    OutboxStatus,
    SagaRunner,
    StatusCountsSource,
)

try:
    __version__ = version("effaced")
except PackageNotFoundError:  # pragma: no cover - only hit on uninstalled source trees
    __version__ = "0.1.0"

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "AbandonedHook",
    "AbandonedSignal",
    "AnonymizationError",
    "AttestingResolver",
    "AuditEvent",
    "AuditEventType",
    "AuditIntegrityError",
    "AuditSink",
    "BackoffPolicy",
    "ColumnEntry",
    "CompletenessFinding",
    "ConfigurationError",
    "ConsentError",
    "ConsentLedger",
    "ConsentRecord",
    "Correction",
    "CoveredField",
    "CoveredSurface",
    "DataMap",
    "DatabaseAuditSink",
    "EffacedError",
    "EffacedStack",
    "EffacedTables",
    "ErasureExecutor",
    "ErasurePlan",
    "ErasurePlanner",
    "ErasureResult",
    "ErasureStep",
    "ErasureStrategy",
    "ErasureVerification",
    "ErasureVerifier",
    "ExportBundle",
    "ExportRecord",
    "Exporter",
    "JoinHop",
    "LegalBasis",
    "LintTarget",
    "ManifestError",
    "Outbox",
    "OutboxEntry",
    "OutboxOperation",
    "OutboxStatus",
    "PiiCategory",
    "PiiSpec",
    "ReachabilityFinding",
    "RectificationExecutor",
    "RectificationResult",
    "RectificationStep",
    "RectificationStepExecutor",
    "Rectifier",
    "RectifyingResolver",
    "RegistryBuild",
    "ReplayPlan",
    "ReplayPlanEntry",
    "ReplaySource",
    "Replayer",
    "Resolver",
    "ResolverErasure",
    "ResolverError",
    "ResolverExport",
    "ResolverRectification",
    "ResolverRegistry",
    "ResolverScheduledErasure",
    "ResolverSpec",
    "RestrictionLedger",
    "RestrictionRecord",
    "RetentionOnlyResolver",
    "RetentionPolicy",
    "RetentionReport",
    "RetentionReportEntry",
    "RetentionSweeper",
    "RetentionViolationError",
    "SagaRunner",
    "SpecOutcome",
    "SqlStatusCountsSource",
    "StatusCountsSource",
    "StepExecutor",
    "SubjectGraph",
    "SubjectLink",
    "SubjectRef",
    "SubjectResolutionError",
    "SurfaceExclusion",
    "SurrogateRegistry",
    "TableAccessPlan",
    "TableEntry",
    "__version__",
    "bind_tables",
    "collect_data_map",
    "default_surrogate_registry",
    "fk_safe_deletion_order",
    "lint_completeness",
    "lint_reachability",
    "load_lint_target",
    "pii",
    "registry_from_settings",
    "resolve_subject_graph",
    "subject_link",
]
