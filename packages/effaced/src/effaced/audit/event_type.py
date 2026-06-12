"""The :class:`AuditEventType` vocabulary."""

from __future__ import annotations

from enum import StrEnum


class AuditEventType(StrEnum):
    """Every kind of event the audit trail records.

    Adding members is a MINOR change; removing or renaming is MAJOR (old
    trails must stay readable forever).
    """

    CONSENT_GRANTED = "consent_granted"
    CONSENT_WITHDRAWN = "consent_withdrawn"
    EXPORT_REQUESTED = "export_requested"
    EXPORT_COMPLETED = "export_completed"
    ERASURE_REQUESTED = "erasure_requested"
    ERASURE_LOCAL_COMPLETED = "erasure_local_completed"
    ERASURE_STEP_SUCCEEDED = "erasure_step_succeeded"
    ERASURE_STEP_FAILED = "erasure_step_failed"
    ERASURE_COMPLETED = "erasure_completed"
    MANIFEST_SNAPSHOT = "manifest_snapshot"
    RECTIFICATION_REQUESTED = "rectification_requested"
    RECTIFICATION_LOCAL_COMPLETED = "rectification_local_completed"
    RECTIFICATION_STEP_SUCCEEDED = "rectification_step_succeeded"
    RECTIFICATION_STEP_FAILED = "rectification_step_failed"
    RECTIFICATION_COMPLETED = "rectification_completed"
    RESTRICTION_PLACED = "restriction_placed"
    RESTRICTION_LIFTED = "restriction_lifted"
    RETENTION_EXPIRED = "retention_expired"
