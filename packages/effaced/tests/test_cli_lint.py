"""The ``effaced lint`` CLI: exit codes and findings on stdout.

Each case loads a tiny module from ``sys.modules`` so the CLI's importlib
loader resolves it without touching the filesystem, then drives ``main`` in
process and asserts the exit code and printed messages.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table
from sqlalchemy.orm import DeclarativeBase, relationship

from effaced import PiiCategory, pii, subject_link
from effaced.cli.main import main

if TYPE_CHECKING:
    from collections.abc import Iterator


def _install(name: str, **attrs: object) -> Iterator[None]:
    """Register a throwaway module exposing ``attrs`` for the import loader."""
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    try:
        yield
    finally:
        del sys.modules[name]


def _clean_base() -> type[DeclarativeBase]:
    """A declarative Base whose only table is fully annotated and reachable."""

    class Base(DeclarativeBase):
        metadata = MetaData()

    Table(
        "people",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Base.registry.map_imperatively(type("Person", (), {}), Base.metadata.tables["people"])
    Base.registry.configure()
    return Base


def _broken_base() -> type[DeclarativeBase]:
    """A Base with an annotated table the planner cannot reach (no anchor)."""

    class Base(DeclarativeBase):
        metadata = MetaData()

    Table(
        "notes",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("body", String, info=pii(PiiCategory.COMMUNICATION)),
        info=subject_link("owner"),
    )
    Base.registry.map_imperatively(type("Note", (), {}), Base.metadata.tables["notes"])
    Base.registry.configure()
    return Base


@pytest.fixture()
def clean_module() -> Iterator[None]:
    yield from _install("cli_fixture_clean", Base=_clean_base())


@pytest.fixture()
def broken_module() -> Iterator[None]:
    yield from _install("cli_fixture_broken", Base=_broken_base())


@pytest.fixture()
def metadata_only_module() -> Iterator[None]:
    metadata = MetaData()
    Table(
        "loose",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("note", String),  # plain, unannotated → a completeness finding
    )
    yield from _install("cli_fixture_meta", metadata=metadata)


def test_clean_target_exits_zero(clean_module: None, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["lint", "cli_fixture_clean:Base"])
    out = capsys.readouterr().out
    assert code == 0
    assert "no findings" in out
    assert "compliant" not in out.lower()


def test_findings_exit_one_and_print_messages(
    broken_module: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["lint", "cli_fixture_broken:Base"])
    out = capsys.readouterr().out
    assert code == 1
    assert "subject graph cannot be resolved" in out
    assert "questions, not verdicts" in out
    assert "compliant" not in out.lower()


def test_metadata_only_skips_reachability(
    metadata_only_module: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["lint", "cli_fixture_meta:metadata"])
    out = capsys.readouterr().out
    assert code == 1  # the completeness finding still fires
    assert "reachability linting was skipped" in out


def test_bad_spec_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["lint", "not-a-spec"])
    err = capsys.readouterr().err
    assert code == 2
    assert "module.path:attribute" in err


def test_missing_attribute_exits_two(
    clean_module: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["lint", "cli_fixture_clean:Nope"])
    err = capsys.readouterr().err
    assert code == 2
    assert "Nope" in err


def test_unimportable_module_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["lint", "no_such_module_xyz:Base"])
    err = capsys.readouterr().err
    assert code == 2
    assert "cannot import" in err


def test_missing_subcommand_exits_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_target_that_is_not_metadata_or_base_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module_name = "cli_fixture_wrong"
    gen = _install(module_name, thing=object())
    next(gen)
    try:
        code = main(["lint", f"{module_name}:thing"])
    finally:
        next(gen, None)
    err = capsys.readouterr().err
    assert code == 2
    assert "neither a SQLAlchemy MetaData nor a declarative Base" in err


def test_a_foreign_key_only_clean_base_is_reachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Base(DeclarativeBase):
        metadata = MetaData()

    Table(
        "people",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "orders",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", Integer, ForeignKey("people.id")),
        info=subject_link("user"),
    )
    person = type("Person2", (), {})
    order = type("Order2", (), {})

    Base.registry.map_imperatively(person, Base.metadata.tables["people"])
    Base.registry.map_imperatively(
        order,
        Base.metadata.tables["orders"],
        properties={"user": relationship(person)},
    )
    Base.registry.configure()
    gen = _install("cli_fixture_fk", Base=Base)
    next(gen)
    try:
        code = main(["lint", "cli_fixture_fk:Base"])
    finally:
        next(gen, None)
    out = capsys.readouterr().out
    assert code == 0
    assert "reachable" in out
