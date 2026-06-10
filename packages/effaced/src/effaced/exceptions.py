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


class AnonymizationError(EffacedError):
    """No type-valid surrogate is registered for a column's type.

    Raised loudly instead of guessing a replacement value — register a
    factory on the :class:`~effaced.adapters.sqlalchemy.SurrogateRegistry`
    for the missing type.
    """


class ConsentError(EffacedError):
    """A consent record is malformed or an operation conflicts with one."""


class ResolverError(EffacedError):
    """An external-system resolver failed in a non-retryable way.

    Retryable failures (timeouts, rate limits) are handled by the saga
    runner and never surface as this exception.
    """


class AuditIntegrityError(EffacedError):
    """The append-only audit log was asked to do something non-append-only.

    Also raised when reading a trail that contains entries this version of
    effaced cannot interpret (e.g. an ``event_type`` recorded by a newer
    release) — unreadable evidence fails loudly instead of being skipped.
    """
