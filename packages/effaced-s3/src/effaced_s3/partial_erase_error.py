"""The :class:`PartialEraseError` — a batch delete that must be retried."""

from __future__ import annotations


class PartialEraseError(Exception):
    """Some object versions under the prefix could not be deleted this attempt.

    Deliberately **not** a :class:`~effaced.exceptions.ResolverError`: the
    saga runner retries any other exception, and a partial batch failure
    is exactly that case — the keys that did delete stay deleted, the
    survivors are re-listed and re-deleted on the next attempt, and
    re-deleting an already-gone version is a no-op, so retries converge.

    Messages carry counts and S3 error codes only — never keys or
    prefixes, which are user content.
    """
