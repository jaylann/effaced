"""The :class:`AuditChainVerifier` — recomputing the tamper-evidence chain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.audit.chain_verification import ChainVerification
from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.audit.hash_chain import compute_event_hash
from effaced.exceptions import AuditIntegrityError

if TYPE_CHECKING:
    from sqlalchemy import RowMapping, Table
    from sqlalchemy.orm import sessionmaker


class AuditChainVerifier:
    """Detects out-of-band modification of recorded audit rows (ADR 0028).

    A standalone read-only verifier — deliberately **not** a method on the
    :class:`~effaced.AuditSink` protocol (which is append-only by
    construction and extends additively only). It follows the
    :class:`~effaced.ReplaySource` precedent: a verification *capability* is a
    separate object reading through the public storage surface, not a
    widening of the sink contract. It writes nothing and appends no event —
    verifying the trail must not modify it.

    It reads the trail in the same total order the sink writes it
    (``occurred_at``, ``event_id`` tiebreak), recomputes each chained row's
    hash from its predecessor, and reports the first row whose stored hash
    does not match — the point an edit, deletion, or reordering breaks the
    chain. Rows written before ADR 0028 (or by a custom sink that does not
    chain) carry no stored hash; they form an *unchained prefix* that is
    skipped, not failed — absence of a hash is absence of evidence, not
    evidence of tampering.

    This **detects** modification of recorded rows; it does not **prevent**
    it. A writer with table access can recompute the chain forward from any
    row they edit. It is a mechanism, never a determination that the trail —
    or the deployment — is secure or compliant.
    """

    def __init__(
        self,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        audit_events: Table,
    ) -> None:
        """Wire the verifier to the trail's session factory and table.

        Args:
            session_factory: Factory producing sessions on the database
                holding the trail — the same one the
                :class:`~effaced.DatabaseAuditSink` writes through.
            audit_events: The ``effaced_audit_events`` table handle from
                :func:`effaced.bind_tables`.
        """
        self._session_factory = session_factory
        self._audit_events = audit_events

    def verify(self) -> ChainVerification:
        """Recompute the whole trail's chain and return the verdict.

        Reads every row in canonical order and recomputes each chained row's
        hash from the prior chained row's stored hash. An unchained prefix
        (rows with a ``NULL`` ``event_hash``) is skipped; chaining begins at
        the first row that carries a stored hash.

        Returns:
            ``verified=True`` with no broken id when every chained row
            recomputed to its stored hash (including a fully unchained
            trail, which verifies vacuously); otherwise ``verified=False``
            naming the first row whose recomputed hash differed.

        Raises:
            AuditIntegrityError: If the trail contains an ``event_type`` this
                version of effaced cannot interpret — all-or-nothing, exactly
                as :meth:`DatabaseAuditSink.read <effaced.DatabaseAuditSink>`
                handles it. An unreadable row cannot be hashed, so the read
                fails loudly rather than verify a partial trail.
        """
        columns = self._audit_events.c
        statement = self._audit_events.select().order_by(
            columns.occurred_at.asc(), columns.event_id.asc()
        )
        with self._session_factory() as session:
            rows = session.execute(statement).mappings().all()

        prior_hash: str | None = None
        for row in rows:
            stored_hash = row["event_hash"]
            if stored_hash is None:
                # Unchained prefix: a legacy or custom-sink row never carried
                # a hash. Skip it and keep the prior link unchanged so the
                # first chained row chains to None.
                continue
            event = self._to_event(row)
            recomputed = compute_event_hash(event, prior_hash)
            if recomputed != stored_hash:
                return ChainVerification(verified=False, first_broken_event_id=event.event_id)
            prior_hash = stored_hash
        return ChainVerification(verified=True)

    @staticmethod
    def _to_event(row: RowMapping) -> AuditEvent:
        """Rebuild one stored row into an :class:`AuditEvent` for hashing."""
        raw_type = row["event_type"]
        try:
            event_type = AuditEventType(raw_type)
        except ValueError as exc:
            msg = (
                f"audit event {row['event_id']} has event_type {raw_type!r}, which this "
                f"version of effaced cannot interpret; upgrade effaced to read this trail"
            )
            raise AuditIntegrityError(msg) from exc
        return AuditEvent(
            event_id=row["event_id"],
            event_type=event_type,
            subject_ref=row["subject_ref"],
            occurred_at=row["occurred_at"],
            payload=row["payload"],
        )
