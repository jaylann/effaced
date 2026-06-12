"""Hypothesis strategies generating annotated schemas effaced rejects.

The sibling ``schema_strategies.py`` deliberately keeps its drawn space
valid — every schema it produces plans and erases cleanly. This module
draws the *rejected* shapes instead, so the never-partial / fail-loudly
contract is evidenced rather than assumed:

- :func:`conflicting_schemas` draws a row-deleted ancestor with a surviving
  child on the subject hop chain — the ADR 0007 planner conflict
  (``RetentionViolationError`` for a retained child, ``ManifestError`` for
  one that merely declares nothing erasable). Detection walks subject hop
  chains only; an off-path FK reference to a row-deleted table is invisible
  to the planner and surfaces solely as a database integrity error at
  execution time, so it is deliberately outside this drawn space.
- :func:`m2m_schemas` draws a many-to-many association table on the subject
  path — :func:`effaced.resolve_subject_graph` refuses the ``secondary=``
  relationship with a :class:`~effaced.exceptions.SubjectResolutionError`
  rather than silently dropping the link table.

Both keep ``GeneratedReject`` self-contained: each carries a ``seed`` that
populates two subjects' rows so a property can prove no DML ran.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from hypothesis import strategies as st
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table
from sqlalchemy.orm import registry, relationship

from effaced import (
    DataMap,
    ErasureStrategy,
    PiiCategory,
    PiiSpec,
    RetentionPolicy,
    collect_data_map,
    pii,
    subject_link,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SUBJECT_TABLE = "t0"
"""The subject table; the deleted ancestor is ``t1`` and the survivor ``t2``."""

_DELETE_SPEC = PiiSpec(category=PiiCategory.IDENTITY, erasure=ErasureStrategy.DELETE)
_RETAIN_SPEC = PiiSpec(
    category=PiiCategory.FINANCIAL,
    erasure=ErasureStrategy.RETAIN,
    retention=RetentionPolicy(reason="§147 AO statutory retention"),
)


class GeneratedReject(NamedTuple):
    """One drawn schema that effaced must reject — never erase partially."""

    metadata: MetaData
    mappers: registry
    classes: dict[str, type]
    """Strong references; the registry holds its mapped classes weakly."""
    data_map: DataMap
    rows: dict[str, int]
    """Rows seeded per subject in each table."""

    def seed(self, session: Session, subject_id: int) -> None:
        """Seed one subject's rows; every cell names its owner via a sentinel."""
        for name, count in self.rows.items():
            table = self.metadata.tables[name]
            for index in range(count):
                session.execute(table.insert().values(**self._row(table, subject_id, index)))

    def _row(self, table: Table, subject_id: int, index: int) -> dict[str, object]:
        name = table.name
        values: dict[str, object] = {"id": subject_id * 1_000 + index}
        if name != SUBJECT_TABLE:
            values["pid"] = subject_id * 1_000
        for column in table.c:
            if column.name not in {"id", "pid"}:
                values[column.name] = f"<s{subject_id}>:{name}.{column.name}#{index}"
        return values


@st.composite
def conflicting_schemas(draw: st.DrawFn) -> GeneratedReject:
    """Draw a row-deleted ancestor with a surviving child on the hop chain.

    ``t1`` is fully PII-owned and all-``DELETE`` (no payload), so the planner
    row-deletes it. ``t2`` reaches the subject via ``t1`` and survives — it
    either retains a column (``RetentionViolationError``) or merely carries a
    payload with nothing erasable declared (``ManifestError``). Either way the
    plan is unsatisfiable: deleting ``t1`` would orphan ``t2``'s rows.
    """
    retained_child = draw(st.booleans())
    metadata = MetaData()
    Table(
        SUBJECT_TABLE,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("c0", String, nullable=False, info=pii(PiiCategory.IDENTITY)),
        info=subject_link(""),
    )
    Table(
        "t1",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("pid", Integer, ForeignKey("t0.id"), nullable=False),
        Column("c0", String, nullable=False, info=_spec_info(_DELETE_SPEC)),
        info=subject_link("parent"),
    )
    _build_survivor(metadata, retained_child=retained_child)
    mappers, classes = _map(metadata, {"t1": "t0", "t2": "t1"})
    return GeneratedReject(
        metadata=metadata,
        mappers=mappers,
        classes=classes,
        data_map=collect_data_map(metadata),
        rows={SUBJECT_TABLE: 1, "t1": draw(st.integers(1, 3)), "t2": draw(st.integers(1, 3))},
    )


def _build_survivor(metadata: MetaData, *, retained_child: bool) -> None:
    """``t2``: a surviving child whose hop chain passes through ``t1``."""
    columns = [
        Column("id", Integer, primary_key=True),
        Column("pid", Integer, ForeignKey("t1.id"), nullable=False),
    ]
    if retained_child:
        columns.append(Column("c0", String, nullable=False, info=_spec_info(_RETAIN_SPEC)))
    else:
        # Survives because it is not fully PII-owned (payload) and declares
        # nothing erasable — the ManifestError branch of the conflict check.
        columns.append(Column("payload", String, nullable=False))
    Table("t2", metadata, *columns, info=subject_link("parent.parent"))


@st.composite
def m2m_schemas(draw: st.DrawFn) -> GeneratedReject:
    """Draw a subject path that joins through a many-to-many secondary table.

    ``t1`` reaches the subject only through an association table, so its
    subject-link path walks a ``secondary=`` relationship —
    :func:`effaced.resolve_subject_graph` rejects it rather than silently
    omitting the link table.
    """
    metadata = MetaData()
    Table(
        SUBJECT_TABLE,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("c0", String, nullable=False, info=pii(PiiCategory.IDENTITY)),
        info=subject_link(""),
    )
    Table(
        "link",
        metadata,
        Column("t0_id", Integer, ForeignKey("t0.id"), primary_key=True),
        Column("t1_id", Integer, ForeignKey("t1.id"), primary_key=True),
    )
    Table(
        "t1",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("c0", String, nullable=False, info=_spec_info(_DELETE_SPEC)),
        info=subject_link("owner"),
    )
    mappers, classes = _map_m2m(metadata)
    return GeneratedReject(
        metadata=metadata,
        mappers=mappers,
        classes=classes,
        data_map=collect_data_map(metadata),
        rows={SUBJECT_TABLE: 1, "t1": draw(st.integers(1, 3))},
    )


def _spec_info(spec: PiiSpec) -> dict[str, object]:
    """Annotate a column through the real ``pii`` helper from a fixed spec."""
    return pii(spec.category, erasure=spec.erasure, retention=spec.retention)


def _map(metadata: MetaData, parents: dict[str, str]) -> tuple[registry, dict[str, type]]:
    """ORM-map a parent-chained schema so subject-link paths resolve."""
    mappers = registry()
    names = list(metadata.tables)
    classes = {name: type(f"Reject_{name}", (), {}) for name in names}
    for name in names:
        table = metadata.tables[name]
        properties = (
            {"parent": relationship(classes[parents[name]], foreign_keys=[table.c.pid])}
            if name in parents
            else {}
        )
        mappers.map_imperatively(classes[name], table, properties=properties)
    mappers.configure()
    return mappers, classes


def _map_m2m(metadata: MetaData) -> tuple[registry, dict[str, type]]:
    """Map ``t1`` to the subject through a ``secondary=`` association table."""
    mappers = registry()
    classes = {name: type(f"Reject_{name}", (), {}) for name in ("t0", "t1")}
    mappers.map_imperatively(classes["t0"], metadata.tables["t0"])
    mappers.map_imperatively(
        classes["t1"],
        metadata.tables["t1"],
        properties={"owner": relationship(classes["t0"], secondary=metadata.tables["link"])},
    )
    mappers.configure()
    return mappers, classes
