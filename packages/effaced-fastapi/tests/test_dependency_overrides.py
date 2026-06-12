"""Session overrides by identity and constructor validation."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_test_app import Base, build_app, build_stack, make_engine, seed_two_users, subject_one
from sqlalchemy.orm import Session, sessionmaker

from effaced import ConfigurationError, EffacedStack
from effaced_fastapi import EffacedFastAPI


def make_gdpr() -> tuple[EffacedFastAPI, list[Session], Callable[[], Iterator[Session]]]:
    """A wired integration plus a session dependency that records its sessions."""
    gdpr = EffacedFastAPI(stack=build_stack(make_engine()))
    seed_two_users(gdpr.stack)
    used: list[Session] = []
    factory = gdpr.stack.session_factory

    def tracking_session() -> Iterator[Session]:
        with factory.begin() as session:
            used.append(session)
            yield session

    return gdpr, used, tracking_session


def test_global_session_override_by_identity() -> None:
    """``app.dependency_overrides[gdpr.session_dependency]`` swaps every route's session."""
    gdpr, used, tracking_session = make_gdpr()
    app = build_app(gdpr)
    app.dependency_overrides[gdpr.session_dependency] = tracking_session
    assert TestClient(app).get("/me/export").status_code == 200
    assert len(used) == 1


def test_per_router_session_override() -> None:
    gdpr, used, tracking_session = make_gdpr()
    app = FastAPI()
    app.include_router(gdpr.router(subject=subject_one, session=tracking_session), prefix="/me")
    assert TestClient(app).get("/me/export").status_code == 200
    assert len(used) == 1


def test_constructor_rejects_stack_plus_arguments() -> None:
    stack = build_stack(make_engine())
    with pytest.raises(ConfigurationError):
        EffacedFastAPI(Base, stack=stack)
    with pytest.raises(ConfigurationError):
        EffacedFastAPI(session_factory=stack.session_factory, stack=stack)


def test_constructor_requires_base_and_session_factory() -> None:
    with pytest.raises(ConfigurationError):
        EffacedFastAPI(Base)
    with pytest.raises(ConfigurationError):
        EffacedFastAPI(session_factory=sessionmaker(make_engine()))
    with pytest.raises(ConfigurationError):
        EffacedFastAPI()


def test_constructor_builds_a_stack_from_base() -> None:
    engine = make_engine()
    gdpr = EffacedFastAPI(Base, sessionmaker(engine))
    assert isinstance(gdpr.stack, EffacedStack)
    assert gdpr.stack.metadata is Base.metadata
