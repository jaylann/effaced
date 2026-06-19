"""The :class:`SubjectLock` protocol — serialize erasures of one subject."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectIdentifier


@runtime_checkable
class SubjectLock(Protocol):
    """Serializes concurrent erasures of one subject and locks its anchor rows.

    Wired into :class:`~effaced.ErasurePlanner` (keyword-only ``lock``), it is
    acquired at the start of :meth:`~effaced.ErasurePlanner.erase_subject`,
    before the first audit event and the first step. It is the mechanism
    behind ADR 0026: an implementation marks the subject as under erasure on a
    durable tombstone it takes a row lock on (so a second concurrent erasure of
    the *same* subject blocks until the first commits), then takes a row lock
    on the subject's anchor rows (so an in-flight application write to the
    subject blocks mid-erasure). Erasures of *different* subjects never
    contend.

    The default :class:`~effaced.ErasurePlanner` has no lock (``lock=None``),
    so existing callers are byte-identical to before this protocol existed;
    wiring a lock is opt-in. The SQLAlchemy implementation is
    :class:`~effaced.adapters.sqlalchemy.SubjectErasureLock`.

    effaced serializes and locks the *local* erasure; it does not determine
    that a subject can never be re-created. Post-completion re-insertion is the
    controller's responsibility, with the tombstone as a detection surface.

    The two methods bracket the local phase: :meth:`acquire` takes the locks
    before the first step, :meth:`mark_erased` records completion after the
    last one. Both run in the caller's session and never commit, so the
    completion mark becomes durable exactly when the erasure does (a rollback
    takes it with it — the tombstone never claims an erasure that did not
    commit).
    """

    def acquire(self, session: Session, subject_ref: SubjectIdentifier) -> None:
        """Take the subject-level locks for an erasure in ``session``.

        Must run inside the caller's open erasure transaction and never
        commit or roll back: the locks are held until the caller's
        transaction ends, which is the whole point — they must outlive
        :meth:`acquire` and cover the local phase. Implementations take
        the tombstone-row lock first, then the anchor-row lock (the ADR 0026
        lock order), so concurrent same-subject erasures serialize and an
        in-flight application write to the subject blocks until the erasure
        commits.

        Idempotent: re-acquiring for an already-tombstoned subject records a
        fresh request and re-takes the locks; "already erased once" is not an
        error (the erasure idempotency contract, ADR 0009).

        Args:
            session: The caller's open erasure session — the same one the
                local phase runs in.
            subject_ref: The subject identifier — a single-column ``str`` or
                a composite :class:`~effaced.CompositeSubjectId`; matched on
                the whole ordered key (ADR 0025).
        """
        ...

    def mark_erased(self, session: Session, subject_ref: SubjectIdentifier) -> None:
        """Record on the tombstone that the local erasure has completed.

        Called by :meth:`~effaced.ErasurePlanner.erase_subject` after the
        local phase succeeds, in the same session, so the completion mark
        commits or rolls back with the erasure — the tombstone never records a
        completion for an erasure that did not commit. It is what turns the
        tombstone into a usable detection surface: a row marked completed
        distinguishes a finished erasure from one still in flight (or one whose
        process crashed mid-erasure, which stays in the requested state).

        Must run inside the caller's open erasure transaction and never commit
        or roll back. Requires :meth:`acquire` to have run first for the same
        subject in this transaction (the row exists and is locked); marking a
        subject never tombstoned is a no-op.

        Args:
            session: The caller's open erasure session.
            subject_ref: The subject identifier — a single-column ``str`` or
                a composite :class:`~effaced.CompositeSubjectId`.
        """
        ...
