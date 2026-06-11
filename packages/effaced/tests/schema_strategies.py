"""Hypothesis strategies generating arbitrary annotated schemas.

The cross-cutting proof suite runs export and erasure against randomly
shaped schemas — table trees, subject-link paths, and per-column erasure
strategies are all drawn — so the suite's invariants are evidenced for any
manifest a user could declare, not just the fixed conftest schema. Every
generated schema goes through the real derivation path
(:func:`effaced.collect_data_map` + :func:`effaced.resolve_subject_graph`);
nothing is hand-built.

Schemas are valid by construction: a table with children always carries an
unannotated ``payload`` column, so it is never fully PII-owned and never
row-deleted — no surviving table's subject path can pass through a
row-deleted ancestor, which keeps the planner's conflict checks (ADR 0007)
out of the drawn space without burning ``assume()`` budget.

Scope: generated schemas are local-database-only — no resolvers, refs, or
outbox legs. Saga re-execution and external-failure semantics are proven
separately (``test_saga_runner_properties.py``,
``test_end_to_end_fault_injection.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from hypothesis import settings
from hypothesis import strategies as st
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table
from sqlalchemy.orm import registry, relationship

from effaced import (
    DataMap,
    ErasureStrategy,
    LegalBasis,
    PiiCategory,
    PiiSpec,
    RetentionPolicy,
    SubjectGraph,
    collect_data_map,
    pii,
    resolve_subject_graph,
    subject_link,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SUBJECT_TABLE = "t0"
"""Name of the generated subject table; non-subject tables are ``t1``, ``t2``, …"""

_STRUCTURAL_COLUMNS = frozenset({"id", "pid", "self_id"})


def scaled_examples(divisor: int) -> int:
    """An example budget relative to the active hypothesis profile.

    Schema-per-example tests are expensive (metadata + ORM mapping + an
    in-memory database per example), so they run a fraction of the
    profile's ``max_examples`` — about 25 on a dev laptop, 75 under the CI
    profile, 625 under the weekly deep profile. Profiles activate in
    ``pytest_configure``, before test modules import, so reading
    ``settings.default`` here scales correctly.
    """
    return max(20, settings.default.max_examples // divisor)


def sentinel(subject_id: int, table: str, column: str, index: int) -> str:
    """A seeded cell value that names its owner — bleed shows up as ``<sN>``."""
    return f"<s{subject_id}>:{table}.{column}#{index}"


class GeneratedSchema(NamedTuple):
    """One drawn annotated schema, derived through the real collection path."""

    metadata: MetaData
    mappers: registry
    classes: dict[str, type]
    """Strong references to the mapped classes — the registry only holds them
    weakly, so dropping these lets GC unmap the schema mid-example."""
    data_map: DataMap
    graph: SubjectGraph
    rows: dict[str, int]
    """Rows seeded per subject in each table (subject table always 1)."""
    parents: dict[str, str]
    """Each non-subject table's parent on the path to the subject."""
    row_deleted_tables: frozenset[str]
    """Tables the planner must whole-row delete (ADR 0007 classification)."""

    @property
    def pii_columns(self) -> dict[str, dict[str, PiiSpec]]:
        """Annotated columns per table, read back from the collected manifest."""
        return {
            entry.name: {column.name: column.spec for column in entry.columns}
            for entry in self.data_map.tables
        }

    @property
    def anonymize_tables(self) -> frozenset[str]:
        """Surviving tables holding at least one non-``RETAIN`` annotated column."""
        return frozenset(
            name
            for name, specs in self.pii_columns.items()
            if name not in self.row_deleted_tables
            and any(spec.erasure is not ErasureStrategy.RETAIN for spec in specs.values())
        )

    @property
    def retain_tables(self) -> frozenset[str]:
        """Surviving tables holding at least one ``RETAIN`` annotated column."""
        return frozenset(
            name
            for name, specs in self.pii_columns.items()
            if any(spec.erasure is ErasureStrategy.RETAIN for spec in specs.values())
        )

    def row_id(self, table: str, subject_id: int, index: int) -> int:
        """Deterministic primary key: the owner is recoverable from the id."""
        if table == SUBJECT_TABLE:
            return subject_id
        return subject_id * 10_000 + int(table.removeprefix("t")) * 100 + index

    def owner(self, table: str, row_id: int) -> int:
        """Which subject a row belongs to, recovered from :meth:`row_id`."""
        return row_id if table == SUBJECT_TABLE else row_id // 10_000

    def seed(self, session: Session, subject_id: int) -> None:
        """Seed one subject's rows; every value carries the owner's sentinel.

        Children attach to their parent's first row; self-referential
        chains stay within the subject, mirroring the conftest schema.
        """
        for name, count in self.rows.items():
            table = self.metadata.tables[name]
            for index in range(count):
                session.execute(table.insert().values(**self._row(table, subject_id, index)))

    def _row(self, table: Table, subject_id: int, index: int) -> dict[str, object]:
        name = table.name
        values: dict[str, object] = {"id": self.row_id(name, subject_id, index)}
        if name != SUBJECT_TABLE:
            values["pid"] = self.row_id(self.parents[name], subject_id, 0)
        if "self_id" in table.c:
            values["self_id"] = self.row_id(name, subject_id, index - 1) if index else None
        for column in table.c:
            if column.name not in _STRUCTURAL_COLUMNS:
                values[column.name] = sentinel(subject_id, name, column.name, index)
        return values


@st.composite
def annotated_schemas(
    draw: st.DrawFn,
    *,
    max_tables: int = 4,
    max_pii_columns: int = 3,
) -> GeneratedSchema:
    """Draw one arbitrary annotated schema (see the module docstring)."""
    count = draw(st.integers(min_value=1, max_value=max_tables))
    names = [f"t{index}" for index in range(count)]
    parents = {
        names[index]: names[draw(st.integers(min_value=0, max_value=index - 1))]
        for index in range(1, count)
    }
    has_children = frozenset(parents.values())
    specs = {name: _draw_specs(draw, max_pii_columns) for name in names}
    with_payload = {name for name in names if name in has_children or draw(st.booleans())}
    with_self_fk = {name for name in names[1:] if draw(st.booleans())}
    rows = {
        name: 1
        if name == SUBJECT_TABLE
        else draw(st.integers(min_value=1 if name in has_children else 0, max_value=3))
        for name in names
    }
    metadata = MetaData()
    for name in names:
        _build_table(
            metadata,
            name,
            parents,
            specs[name],
            payload=name in with_payload,
            self_fk=name in with_self_fk,
        )
    mappers, classes = _map_classes(metadata, names, parents)
    data_map = collect_data_map(metadata)
    row_deleted = frozenset(
        name
        for name in names
        if name not in with_payload
        and all(spec.erasure is ErasureStrategy.DELETE for spec in specs[name].values())
    )
    return GeneratedSchema(
        metadata=metadata,
        mappers=mappers,
        classes=classes,
        data_map=data_map,
        graph=resolve_subject_graph(data_map, mappers),
        rows=rows,
        parents=parents,
        row_deleted_tables=row_deleted,
    )


def _draw_specs(draw: st.DrawFn, max_pii_columns: int) -> dict[str, PiiSpec]:
    """Draw a table's annotated columns: mixed strategies, optional Art. 15 detail."""
    count = draw(st.integers(min_value=0, max_value=max_pii_columns))
    specs: dict[str, PiiSpec] = {}
    for index in range(count):
        erasure = draw(st.sampled_from(ErasureStrategy))
        retention = (
            RetentionPolicy(reason=f"legal duty {index}")
            if erasure is ErasureStrategy.RETAIN
            else None
        )
        specs[f"c{index}"] = PiiSpec(
            category=draw(st.sampled_from(PiiCategory)),
            erasure=erasure,
            retention=retention,
            legal_basis=draw(st.none() | st.sampled_from(LegalBasis)),
            purpose=draw(st.none() | st.just("proof suite")),
        )
    return specs


def _build_table(
    metadata: MetaData,
    name: str,
    parents: dict[str, str],
    specs: dict[str, PiiSpec],
    *,
    payload: bool,
    self_fk: bool,
) -> Table:
    """One generated table, annotated through the real ``pii``/``subject_link``."""
    columns = [Column("id", Integer, primary_key=True)]
    if name != SUBJECT_TABLE:
        columns.append(Column("pid", Integer, ForeignKey(f"{parents[name]}.id"), nullable=False))
    if self_fk:
        columns.append(Column("self_id", Integer, ForeignKey(f"{name}.id"), nullable=True))
    if payload:
        columns.append(Column("payload", String, nullable=False))
    columns.extend(
        Column(
            column_name,
            String,
            nullable=False,
            info=pii(
                spec.category,
                erasure=spec.erasure,
                retention=spec.retention,
                legal_basis=spec.legal_basis,
                purpose=spec.purpose,
            ),
        )
        for column_name, spec in specs.items()
    )
    return Table(name, metadata, *columns, info=subject_link(_path(name, parents)))


def _path(name: str, parents: dict[str, str]) -> str:
    """The dotted relationship path from one table up to the subject table."""
    segments: list[str] = []
    while name != SUBJECT_TABLE:
        segments.append("parent")
        name = parents[name]
    return ".".join(segments)


def _map_classes(
    metadata: MetaData, names: list[str], parents: dict[str, str]
) -> tuple[registry, dict[str, type]]:
    """ORM-map every generated table so subject-link paths resolve for real.

    Returns the classes alongside the registry: the registry references its
    mapped classes weakly, so the caller must keep them alive.
    """
    mappers = registry()
    classes = {name: type(f"Generated_{name}", (), {}) for name in names}
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
