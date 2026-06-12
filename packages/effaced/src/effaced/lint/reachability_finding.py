"""The :class:`ReachabilityFinding` lint result."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ReachabilityFinding(BaseModel):
    """One place the erasure planner could not route back to a subject.

    Emitted by :func:`effaced.adapters.sqlalchemy.lint_reachability`. A table
    can be fully annotated yet have no subject-link path the planner
    (:func:`~effaced.adapters.sqlalchemy.resolve_subject_graph`) can walk back
    to a subject anchor — its data would then be silently never erased. Like a
    :class:`~effaced.lint.CompletenessFinding`, a finding is a question, not a
    verdict: it names a gap so a human can fix the annotation or the path.
    effaced never decides on your behalf that data is unreachable on purpose.

    Attributes:
        table: Name of the store the finding points at, or ``None`` when the
            finding is graph-level (no subject anchor, or a foreign-key cycle
            that has no single table to attribute it to) — mirroring
            :attr:`~effaced.lint.CompletenessFinding.column` being ``None`` for
            a whole-table finding.
        reason: Why the planner could not reach the subject from this table,
            phrased as the resolver's own diagnostic message.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1)

    @property
    def message(self) -> str:
        """A human-readable one-liner, written for CI logs."""
        if self.table is None:
            return f"subject graph cannot be resolved: {self.reason}"
        return f"table {self.table!r} is unreachable from the subject: {self.reason}"
