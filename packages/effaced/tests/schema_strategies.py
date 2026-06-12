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
out of the drawn space without burning ``assume()`` budget. The *rejected*
shapes — a row-deleted ancestor with a surviving child, and many-to-many
secondary tables — are drawn (and shown to fail loudly, never partially)
by ``rejected_schema_strategies.py`` and its properties instead.

The valid space spans deep self-referential chains and composite
foreign-key hops. Both the subject table and non-subject tables may carry a
``self_id`` self-FK; self-FK tables seed up to five rows, so a chain runs to
depth 4 within one subject. A composite table has an ``(id, id2)`` primary
key and its children reference both columns through a
``ForeignKeyConstraint`` — exercising :class:`JoinHop` column tuples and the
``scoping.grouped`` row-value tuples in every shared invariant. Composite
*subject* keys are out of scope: :class:`SubjectGraph` carries a single
``subject_id_column``.

Scope: generated schemas are local-database-only — no resolvers, refs, or
outbox legs. Saga re-execution and external-failure semantics are proven
separately (``test_saga_runner_properties.py``,
``test_end_to_end_fault_injection.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from hypothesis import settings
from hypothesis import strategies as st
from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
)
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

_STRUCTURAL_COLUMNS = frozenset({"id", "id2", "pid", "pid2", "self_id", "self_id2"})

_SELF_FK_ROW_MAX = 5
"""Per-subject row cap for self-FK tables — deep enough for depth-4 chains."""

_ROW_MAX = 3
"""Per-subject row cap for tables without a self-FK chain."""


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
    composite_tables: frozenset[str]
    """Tables with a composite ``(id, id2)`` primary key — their children
    join on both columns, exercising composite ``JoinHop`` pairs."""

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
        if name in self.composite_tables:
            # Seed the second key half deterministically so the owner stays
            # recoverable from ``id`` alone and the composite FK still joins.
            values["id2"] = subject_id
        if name != SUBJECT_TABLE:
            parent = self.parents[name]
            values["pid"] = self.row_id(parent, subject_id, 0)
            if parent in self.composite_tables:
                values["pid2"] = subject_id
        if "self_id" in table.c:
            values["self_id"] = self.row_id(name, subject_id, index - 1) if index else None
        if "self_id2" in table.c:
            # Composite self-FK references (id, id2); id2 is the subject id.
            values["self_id2"] = subject_id if index else None
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
    # Self-FK is allowed on the subject table too — its single seeded row
    # carries a null self_id, exercising the self-referential hop chain on
    # the subject table itself without changing its one-row-per-subject seed.
    with_self_fk = {name for name in names if draw(st.booleans())}
    # Composite (id, id2) primary keys only on non-subject tables: the
    # subject identity stays a single column (SubjectGraph.subject_id_column).
    composite = {name for name in names[1:] if draw(st.booleans())}
    rows = {name: _row_count(draw, name, has_children, with_self_fk) for name in names}
    metadata = MetaData()
    for name in names:
        shape = _TableShape(
            payload=name in with_payload,
            self_fk=name in with_self_fk,
            composite=name in composite,
        )
        _build_table(metadata, name, parents, specs[name], shape, composite)
    mappers, classes = _map_classes(metadata, names, parents, composite)
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
        composite_tables=frozenset(composite),
    )


def _row_count(
    draw: st.DrawFn,
    name: str,
    has_children: frozenset[str],
    with_self_fk: frozenset[str],
) -> int:
    """Per-subject row count: the subject table is always one row.

    Self-FK tables seed up to five rows so a self-referential chain reaches
    depth 4 within one subject; other tables keep the original cap.
    """
    if name == SUBJECT_TABLE:
        return 1
    maximum = _SELF_FK_ROW_MAX if name in with_self_fk else _ROW_MAX
    return draw(st.integers(min_value=1 if name in has_children else 0, max_value=maximum))


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


class _TableShape(NamedTuple):
    """The drawn structural choices for one generated table."""

    payload: bool
    self_fk: bool
    composite: bool


def _build_table(
    metadata: MetaData,
    name: str,
    parents: dict[str, str],
    specs: dict[str, PiiSpec],
    shape: _TableShape,
    composite_parents: frozenset[str],
) -> Table:
    """One generated table, annotated through the real ``pii``/``subject_link``.

    A composite table carries an ``(id, id2)`` primary key; a child of a
    composite parent references both halves through a ``ForeignKeyConstraint``
    so the resolved subject path is built from composite ``JoinHop`` pairs.
    """
    parent_composite = name != SUBJECT_TABLE and parents[name] in composite_parents
    columns: list[Column[Integer] | Column[String]] = [
        Column("id", Integer, primary_key=True, autoincrement=False)
    ]
    if shape.composite:
        columns.append(Column("id2", Integer, primary_key=True, autoincrement=False))
    columns.extend(_parent_fk_columns(name, parents, parent_composite=parent_composite))
    columns.extend(_self_fk_columns(name, self_fk=shape.self_fk, composite=shape.composite))
    if shape.payload:
        columns.append(Column("payload", String, nullable=False))
    columns.extend(_pii_columns(specs))
    constraints = _composite_constraints(
        name,
        parents,
        self_fk=shape.self_fk,
        composite=shape.composite,
        parent_composite=parent_composite,
    )
    return Table(name, metadata, *columns, *constraints, info=subject_link(_path(name, parents)))


def _parent_fk_columns(
    name: str, parents: dict[str, str], *, parent_composite: bool
) -> list[Column[Integer]]:
    """The pid (and composite pid2) columns linking a child to its parent."""
    if name == SUBJECT_TABLE:
        return []
    if parent_composite:
        # Both halves; the ForeignKeyConstraint is declared on the table.
        return [Column("pid", Integer, nullable=False), Column("pid2", Integer, nullable=False)]
    return [Column("pid", Integer, ForeignKey(f"{parents[name]}.id"), nullable=False)]


def _self_fk_columns(name: str, *, self_fk: bool, composite: bool) -> list[Column[Integer]]:
    """The self_id (and composite self_id2) self-referential FK columns."""
    if not self_fk:
        return []
    if composite:
        # Both halves; the ForeignKeyConstraint is declared on the table.
        return [
            Column("self_id", Integer, nullable=True),
            Column("self_id2", Integer, nullable=True),
        ]
    return [Column("self_id", Integer, ForeignKey(f"{name}.id"), nullable=True)]


def _composite_constraints(
    name: str,
    parents: dict[str, str],
    *,
    self_fk: bool,
    composite: bool,
    parent_composite: bool,
) -> list[ForeignKeyConstraint]:
    """Composite foreign-key constraints (parent hop and, if any, self hop)."""
    constraints: list[ForeignKeyConstraint] = []
    if parent_composite:
        parent = parents[name]
        constraints.append(ForeignKeyConstraint(["pid", "pid2"], [f"{parent}.id", f"{parent}.id2"]))
    if composite and self_fk:
        constraints.append(
            ForeignKeyConstraint(["self_id", "self_id2"], [f"{name}.id", f"{name}.id2"])
        )
    return constraints


def _pii_columns(specs: dict[str, PiiSpec]) -> list[Column[String]]:
    """The annotated PII columns, declared through the real ``pii`` helper."""
    return [
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
    ]


def _path(name: str, parents: dict[str, str]) -> str:
    """The dotted relationship path from one table up to the subject table."""
    segments: list[str] = []
    while name != SUBJECT_TABLE:
        segments.append("parent")
        name = parents[name]
    return ".".join(segments)


def _map_classes(
    metadata: MetaData, names: list[str], parents: dict[str, str], composite: frozenset[str]
) -> tuple[registry, dict[str, type]]:
    """ORM-map every generated table so subject-link paths resolve for real.

    A child of a composite parent maps its ``parent`` relationship over both
    foreign-key columns, so the resolved hop carries a composite column pair.

    Returns the classes alongside the registry: the registry references its
    mapped classes weakly, so the caller must keep them alive.
    """
    mappers = registry()
    classes = {name: type(f"Generated_{name}", (), {}) for name in names}
    for name in names:
        table = metadata.tables[name]
        properties: dict[str, object] = {}
        if name in parents:
            foreign_keys = (
                [table.c.pid, table.c.pid2] if parents[name] in composite else [table.c.pid]
            )
            properties["parent"] = relationship(classes[parents[name]], foreign_keys=foreign_keys)
        mappers.map_imperatively(classes[name], table, properties=properties)
    mappers.configure()
    return mappers, classes
