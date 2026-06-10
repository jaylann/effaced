"""Exception hierarchy for effaced.

Every error raised by the library derives from :class:`EffacedError` so
callers can catch the whole family with one clause.
"""

from __future__ import annotations


class EffacedError(Exception):
    """Base class for all effaced errors."""


class ManifestError(EffacedError):
    """The declared data map is invalid, incomplete, or unmigratable."""


class SubjectResolutionError(EffacedError):
    """A table's subject link path could not be resolved to the subject."""


class RetentionViolationError(EffacedError):
    """An operation would delete data that is under a legal retention duty.

    This is always a bug in the caller or the manifest — effaced refuses
    to proceed rather than silently destroying retained records.
    """


class ConsentError(EffacedError):
    """A consent record is malformed or an operation conflicts with one."""


class ResolverError(EffacedError):
    """An external-system resolver failed in a non-retryable way.

    Retryable failures (timeouts, rate limits) are handled by the saga
    runner and never surface as this exception.
    """


class AuditIntegrityError(EffacedError):
    """The append-only audit log was asked to do something non-append-only."""
