"""The reachability linter flags every table the planner cannot reach.

Each test builds a deliberately broken schema, derives its manifest through
the real collector, and asserts the linter names the gap — the exact inverse
of :func:`resolve_subject_graph` succeeding.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, registry, relationship

from effaced import (
    PiiCategory,
    SubjectResolutionError,
    collect_data_map,
    lint_reachability,
    pii,
    resolve_subject_graph,
    subject_link,
)
from effaced.adapters.sqlalchemy import INFO_KEY
from effaced.exceptions import ManifestError


def _map(metadata: MetaData, links: dict[str, dict[str, object]]) -> registry:
    """Imperatively map every table; ``links`` gives relationship properties."""
    mappers = registry()
    classes = {name: type(f"M_{name}", (), {}) for name in metadata.tables}
    for name, table in metadata.tables.items():
        properties = {
            key: relationship(classes[target]) for key, target in links.get(name, {}).items()
        }
        mappers.map_imperatively(classes[name], table, properties=properties)
    mappers.configure()
    # Keep classes alive: the registry references mapped classes only weakly.
    mappers._effaced_classes = classes  # type: ignore[attr-defined]  # strong ref for the test
    return mappers


def test_clean_schema_yields_no_findings(metadata: MetaData, orm_registry: registry) -> None:
    data_map = collect_data_map(metadata)
    assert lint_reachability(data_map, orm_registry) == ()


def test_no_subject_anchor_is_one_graph_level_finding() -> None:
    metadata = MetaData()
    Table(
        "notes",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("body", String, info=pii(PiiCategory.COMMUNICATION)),
        info=subject_link("owner"),
    )
    mappers = _map(metadata, {})
    findings = lint_reachability(collect_data_map(metadata), mappers)
    assert len(findings) == 1
    assert findings[0].table is None
    assert "no subject table" in findings[0].reason


def test_multiple_anchors_flag_every_extra() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "accounts",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("login", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    mappers = _map(metadata, {})
    findings = lint_reachability(collect_data_map(metadata), mappers)
    # Exactly one extra anchor is flagged (the first declared one is the anchor).
    assert len(findings) == 1
    assert findings[0].table in {"people", "accounts"}
    assert "more than one" in findings[0].reason


def test_pii_table_without_subject_link_is_flagged() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "orphan",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("note", String, info=pii(PiiCategory.COMMUNICATION)),
    )
    mappers = _map(metadata, {})
    findings = lint_reachability(collect_data_map(metadata), mappers)
    assert [finding.table for finding in findings] == ["orphan"]
    assert "declares no" in findings[0].reason or "subject_link" in findings[0].reason


def test_path_not_ending_at_subject_is_flagged() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "teams",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("person_id", Integer, ForeignKey("people.id")),
        info=subject_link("owner"),
    )
    Table(
        "memberships",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("team_id", Integer, ForeignKey("teams.id")),
        Column("seat", String, info=pii(PiiCategory.IDENTITY)),
        info=subject_link("team"),
    )
    mappers = _map(metadata, {"memberships": {"team": "teams"}, "teams": {"owner": "people"}})
    findings = lint_reachability(collect_data_map(metadata), mappers)
    flagged = {finding.table for finding in findings}
    # 'memberships' path stops at 'teams' (not the subject); flagged for it.
    assert "memberships" in flagged
    membership = next(f for f in findings if f.table == "memberships")
    assert "ends at" in membership.reason


def test_m2m_secondary_path_is_flagged() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    link = Table(
        "people_tags",
        metadata,
        Column("person_id", Integer, ForeignKey("people.id"), primary_key=True),
        Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
    )
    Table(
        "tags",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("label", String, info=pii(PiiCategory.IDENTITY)),
        info=subject_link("people"),
    )
    mappers = registry()
    classes = {name: type(f"M_{name}", (), {}) for name in ("people", "tags")}
    mappers.map_imperatively(classes["people"], metadata.tables["people"])
    mappers.map_imperatively(
        classes["tags"],
        metadata.tables["tags"],
        properties={"people": relationship(classes["people"], secondary=link)},
    )
    mappers.configure()
    mappers._effaced_classes = classes  # type: ignore[attr-defined]  # strong ref for the test
    findings = lint_reachability(collect_data_map(metadata), mappers)
    assert "tags" in {finding.table for finding in findings}
    assert any("secondary" in finding.reason for finding in findings)


def test_unmapped_table_is_flagged() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "ghost",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("person_id", Integer, ForeignKey("people.id")),
        Column("note", String, info=pii(PiiCategory.COMMUNICATION)),
        info=subject_link("person"),
    )
    mappers = registry()
    person = type("M_people", (), {})
    mappers.map_imperatively(person, metadata.tables["people"])
    mappers.configure()
    mappers._effaced_people = person  # type: ignore[attr-defined]  # strong ref for the test
    findings = lint_reachability(collect_data_map(metadata), mappers)
    assert "ghost" in {finding.table for finding in findings}
    assert any("not mapped" in finding.reason for finding in findings)


def test_missing_subject_id_column_is_flagged() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("pk", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link("", subject_id_column="missing"),
    )
    mappers = _map(metadata, {})
    findings = lint_reachability(collect_data_map(metadata), mappers)
    assert [finding.table for finding in findings] == ["people"]
    assert "subject_id_column" in findings[0].reason


def test_messages_name_the_gap() -> None:
    metadata = MetaData()
    Table(
        "notes",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("body", String, info=pii(PiiCategory.COMMUNICATION)),
        info=subject_link("owner"),
    )
    mappers = _map(metadata, {})
    findings = lint_reachability(collect_data_map(metadata), mappers)
    assert "subject graph cannot be resolved" in findings[0].message


def test_unmapped_subject_anchor_is_flagged() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    # An empty registry: the lone anchor table is annotated but never mapped.
    mappers = registry()
    findings = lint_reachability(collect_data_map(metadata), mappers)
    assert [finding.table for finding in findings] == ["people"]
    assert "not mapped" in findings[0].reason


@pytest.mark.filterwarnings("ignore:Cannot correctly sort tables")
def test_foreign_key_cycle_is_a_graph_level_finding() -> None:
    # A declarative Base shares Base.registry.metadata with Base.metadata, so the
    # FK edges are visible to the cycle check (a bare imperative registry carries
    # an empty metadata and could not surface a cross-table cycle at all).
    class Base(DeclarativeBase):
        metadata = MetaData()

    class Person(Base):
        __tablename__ = "people"
        __table_args__: ClassVar[dict[str, object]] = {"info": subject_link("")}

        id: Mapped[int] = mapped_column(primary_key=True)
        partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
        email: Mapped[str] = mapped_column(info=pii(PiiCategory.CONTACT))
        partner: Mapped[Partner | None] = relationship(foreign_keys=[partner_id])

    class Partner(Base):
        __tablename__ = "partners"
        __table_args__: ClassVar[dict[str, object]] = {"info": subject_link("person")}

        id: Mapped[int] = mapped_column(primary_key=True)
        person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
        label: Mapped[str] = mapped_column(info=pii(PiiCategory.IDENTITY))
        person: Mapped[Person] = relationship(foreign_keys=[person_id])

    findings = lint_reachability(collect_data_map(Base.metadata), Base.registry)
    cycle = [finding for finding in findings if finding.table is None]
    assert len(cycle) == 1
    assert "cycle" in cycle[0].reason
    assert "cannot be resolved" in cycle[0].message


def test_table_level_finding_message_names_the_table() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "orphan",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("note", String, info=pii(PiiCategory.COMMUNICATION)),
    )
    mappers = _map(metadata, {})
    orphan = next(f for f in lint_reachability(collect_data_map(metadata), mappers) if f.table)
    assert "'orphan'" in orphan.message
    assert "unreachable from the subject" in orphan.message


def test_malformed_annotation_raises_like_the_collector() -> None:
    metadata = MetaData()
    Table(
        "broken",
        metadata,
        Column("id", Integer, primary_key=True),
        info={INFO_KEY: "not a SubjectLink"},
    )
    mappers = _map(metadata, {})
    with pytest.raises(ManifestError, match="broken"):
        lint_reachability(collect_data_map(metadata), mappers)


def test_findings_match_resolution_failure_on_a_broken_schema() -> None:
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "orphan",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("note", String, info=pii(PiiCategory.COMMUNICATION)),
    )
    mappers = _map(metadata, {})
    data_map = collect_data_map(metadata)
    assert lint_reachability(data_map, mappers) != ()
    with pytest.raises(SubjectResolutionError):
        resolve_subject_graph(data_map, mappers)
