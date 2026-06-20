"""The :class:`AuditChainVerifier` — recomputing the tamper-evidence chain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.audit.chain_verification import ChainVerification
from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.audit.hash_chain import compute_event_hash
from effaced.exceptions import AuditIntegrityError

if TYPE_CHECKING:
    from collections.abc import Sequence

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

    It follows the chain as a *linked list* keyed by insertion — each row's
    ``prior_hash`` points at its predecessor's ``event_hash``, exactly as the
    sink wrote it — never by ``occurred_at`` (which is caller-supplied and
    backdatable, so it cannot order the chain). Starting from the genesis row
    (``prior_hash IS NULL``) it walks predecessor→successor pointers,
    recomputing each row's hash from its stored ``prior_hash`` and content,
    and reports the first row whose stored hash does not match — the point an
    edit, deletion, or reordering breaks the chain. A *fork* (two rows citing
    the same predecessor, e.g. concurrent appends) and an *orphan* (a chained
    row unreachable from genesis, e.g. a deleted link) are likewise breaks,
    surfaced and never silently healed.

    Rows written before ADR 0028 (or by a custom sink that does not chain)
    carry no stored hash; they are *unchained* — skipped entirely, not
    failed. Absence of a hash is absence of evidence, not evidence of
    tampering, so a trail with no chained rows verifies vacuously.

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
        """Walk the trail's hash chain and return the verdict.

        Reads every chained row (``event_hash`` not ``NULL``), follows the
        linked list from genesis (``prior_hash IS NULL``) along
        predecessor→successor pointers, and recomputes each row's hash from
        its stored ``prior_hash`` and content. Unchained rows (``NULL``
        ``event_hash``) are ignored; a trail with no chained rows verifies
        vacuously.

        Returns:
            ``verified=True`` with no broken id when the chained rows form a
            single intact list whose every row recomputes to its stored hash
            (including the empty/fully-unchained case); otherwise
            ``verified=False`` naming the first broken row — the earliest
            content mismatch along the walk, or the fork/orphan/missing-or-
            duplicate-genesis row that proves the list is no longer a single
            intact chain.

        Raises:
            AuditIntegrityError: If the trail contains an ``event_type`` this
                version of effaced cannot interpret — all-or-nothing, exactly
                as :meth:`DatabaseAuditSink.read <effaced.DatabaseAuditSink>`
                handles it. An unreadable row cannot be hashed, so the read
                fails loudly rather than verify a partial trail.
        """
        columns = self._audit_events.c
        statement = self._audit_events.select().where(columns.event_hash.isnot(None))
        with self._session_factory() as session:
            rows = session.execute(statement).mappings().all()

        if not rows:
            return ChainVerification(verified=True)
        return self._walk(rows)

    def _walk(self, rows: Sequence[RowMapping]) -> ChainVerification:
        """Follow the linked list from genesis, validating each hash.

        ``rows`` is the non-empty set of chained rows in any order. The walk
        is bounded by ``len(rows)`` so a tampered ``prior_hash`` forming a
        cycle terminates instead of looping forever.
        """
        children_by_prior: dict[str | None, list[RowMapping]] = {}
        for row in rows:
            children_by_prior.setdefault(row["prior_hash"], []).append(row)

        # Genesis: exactly one chained row with prior_hash IS NULL. Zero means
        # the head was deleted/repointed (every row cites a predecessor, none
        # of which is a genesis); two or more is a fork at the head.
        genesis = children_by_prior.get(None, [])
        if len(genesis) != 1:
            return self._break(genesis[0]) if genesis else self._earliest_break(rows)

        visited = 0
        current: RowMapping | None = genesis[0]
        while current is not None and visited < len(rows):
            event = self._to_event(current)
            if compute_event_hash(event, current["prior_hash"]) != current["event_hash"]:
                return self._break(current)
            visited += 1
            children = children_by_prior.get(current["event_hash"], [])
            if len(children) > 1:
                # A fork: two rows chain to the same predecessor.
                return self._break(children[0])
            current = children[0] if children else None

        if visited != len(rows):
            # Orphan rows unreachable from genesis (a deleted/repointed link).
            return self._earliest_break(rows)
        return ChainVerification(verified=True)

    def _earliest_break(self, rows: Sequence[RowMapping]) -> ChainVerification:
        """Report a deterministic broken id when the list has no single head.

        Used when there is no genesis or rows are orphaned — there is no
        walk order to follow, so the smallest ``event_id`` is the stable,
        reproducible choice for *which* row to name as the break.
        """
        earliest = min(rows, key=lambda row: row["event_id"])
        return self._break(earliest)

    def _break(self, row: RowMapping) -> ChainVerification:
        """A failing verdict naming ``row`` as the first broken event."""
        return ChainVerification(verified=False, first_broken_event_id=row["event_id"])

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
