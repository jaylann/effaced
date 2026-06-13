"""End-to-end: the reused SQLAlchemy executors erase a Django-derived schema.

This is the load-bearing proof of the adapter — the engine, wired from Django
model metadata, performs a real subject erasure over SQLite with no
cross-subject bleed, exercising the foreign-key-resolved subject graph and the
unchanged executors together.
"""

from __future__ import annotations

import pytest
from django_fixture_models import TEST_REGISTRY
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from effaced_django import DjangoEffacedStack


@pytest.fixture
def stack() -> DjangoEffacedStack:
    engine: Engine = create_engine("sqlite://", poolclass=StaticPool)
    session_factory = sessionmaker(engine)
    built = DjangoEffacedStack.from_models(session_factory, annotations=TEST_REGISTRY.annotations)
    built.metadata.create_all(engine)
    users = built.metadata.tables["django_users"]
    posts = built.metadata.tables["django_posts"]
    comments = built.metadata.tables["django_comments"]
    with session_factory.begin() as session:
        session.execute(
            users.insert(),
            [
                {"id": 1, "email": "alice@example.com", "display_name": "Alice"},
                {"id": 2, "email": "bob@example.com", "display_name": "Bob"},
            ],
        )
        session.execute(
            posts.insert(),
            [{"id": 1, "author_id": 1, "body": "a"}, {"id": 2, "author_id": 2, "body": "b"}],
        )
        session.execute(
            comments.insert(),
            [{"id": 1, "post_id": 1, "text": "ca"}, {"id": 2, "post_id": 2, "text": "cb"}],
        )
    return built


def test_subject_graph_is_fk_safe(stack: DjangoEffacedStack) -> None:
    assert stack.graph.subject_table == "django_users"
    assert stack.graph.deletion_order == ("django_comments", "django_posts", "django_users")


def test_erase_one_subject_leaves_the_other_untouched(stack: DjangoEffacedStack) -> None:
    with stack.session_factory.begin() as session:
        result = stack.planner.erase_subject(session, "1")

    # fully-PII-owned tables are row-deleted, scoped to subject 1 only
    assert result.deleted.get("django_users") == 1
    assert result.deleted.get("django_posts") == 1
    assert result.deleted.get("django_comments") == 1

    metadata = stack.metadata
    with stack.session_factory() as session:
        remaining_users = session.execute(
            select(func.count()).select_from(metadata.tables["django_users"])
        ).scalar_one()
        remaining_comments = (
            session.execute(select(metadata.tables["django_comments"].c.id)).scalars().all()
        )
    assert remaining_users == 1  # Bob survives
    assert remaining_comments == [2]  # only Bob's comment remains — no bleed
