"""The :class:`EffacedFastAPI` integration — engines wired, endpoints mounted."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from effaced import (
    ConfigurationError,
    ConsentRecord,
    EffacedStack,
    ErasureResult,
    ExportBundle,
    RestrictionRecord,
)
from effaced_fastapi.consent_request import ConsentRequest
from effaced_fastapi.restriction_request import RestrictionRequest
from effaced_fastapi.saga_worker import SagaWorker
from effaced_fastapi.subject import Subject, SubjectProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from contextlib import AbstractAsyncContextManager

    from fastapi import FastAPI
    from sqlalchemy.orm import DeclarativeBase, sessionmaker

    from effaced.resolvers.base import Resolver

SOURCE = "api"
"""The ``source`` recorded on consent and restriction events this router writes."""


def _session_dependency(
    session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
) -> Callable[[], Iterator[Session]]:
    """Build the default one-transaction-per-request session dependency."""

    def get_session() -> Iterator[Session]:
        """One transaction per request — commits on success, rolls back on error."""
        with session_factory.begin() as session:
            yield session

    return get_session


class EffacedFastAPI:
    """Mounts effaced's data-subject endpoints on a FastAPI application.

    Wires the full :class:`effaced.EffacedStack` from your declarative
    base (or accepts one you prewired) and exposes the trigger points as a
    router. Every route is a plain ``def``: FastAPI runs it on its
    threadpool, so the sync engines (ADR 0006) never block the event loop
    — and your subject provider may still be ``async``.

    The integration is deliberately thin: requests are served by the same
    engines, with the same audit, idempotency, and retention contracts,
    that you could call by hand. Route paths and response shapes are
    public API — the responses are the engines' own result models, so
    what an endpoint returns changes only when the underlying engine's
    behaviour does (widened SemVer).

    Attributes:
        session_dependency: The default per-request transaction dependency
            (``with session_factory.begin()``). Built once, so it can be
            overridden by identity via ``app.dependency_overrides``.
    """

    session_dependency: Callable[[], Iterator[Session]]

    def __init__(
        self,
        base: type[DeclarativeBase] | None = None,
        session_factory: sessionmaker | None = None,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        *,
        resolvers: Sequence[Resolver] = (),
        stack: EffacedStack | None = None,
    ) -> None:
        """Wire the integration from a base or adopt a prewired stack.

        Args:
            base: The declarative base carrying the :func:`effaced.pii` /
                :func:`effaced.subject_link` annotations.
            session_factory: Factory producing sessions on the application
                database.
            resolvers: External-system resolvers to register, by instance.
            stack: A prewired :class:`effaced.EffacedStack` — for custom
                audit sinks or a settings-driven registry. Mutually
                exclusive with the other arguments.

        Raises:
            ConfigurationError: If both a ``stack`` and construction
                arguments are given, or if ``base``/``session_factory``
                are missing when no ``stack`` is given.
        """
        if stack is not None:
            if base is not None or session_factory is not None or resolvers:
                msg = "pass either a prewired stack or (base, session_factory), not both"
                raise ConfigurationError(msg)
        else:
            if base is None or session_factory is None:
                msg = "base and session_factory are required when no stack is given"
                raise ConfigurationError(msg)
            stack = EffacedStack.from_base(base, session_factory, resolvers=resolvers)
        self._stack = stack
        self.session_dependency = _session_dependency(stack.session_factory)

    @property
    def stack(self) -> EffacedStack:
        """The wired engines, for direct calls beyond the endpoints."""
        return self._stack

    def router(
        self,
        subject: SubjectProvider,
        *,
        session: Callable[..., Iterator[Session]] | None = None,
        restriction: bool = False,
        tags: Sequence[str] = ("gdpr",),
    ) -> APIRouter:
        """Build the data-subject router around your subject provider.

        Include it with a prefix that scopes it to the authenticated user,
        e.g. ``app.include_router(gdpr.router(subject=...), prefix="/me")``
        — the erasure route lives at the prefix root (``DELETE /me``).

        Default endpoints: ``POST /consent`` (record grant/withdrawal),
        ``GET /consent/{purpose}`` (current status), ``GET /export``
        (Art. 15 bundle), and ``DELETE`` at the prefix root (Art. 17
        erasure). Rectification ships no endpoint: which corrections a
        subject may self-serve is an authorization decision your
        application owns — call :meth:`effaced.Rectifier.rectify_subject`
        from your own route.

        Args:
            subject: Dependency resolving the request's
                :class:`~effaced_fastapi.Subject` — your auth decides who
                the subject is and which external refs they carry.
            session: Per-router override of :attr:`session_dependency`.
            restriction: Also expose ``POST /restriction`` and
                ``GET /restriction`` (Art. 18 flag-keeping, ADR 0014).
            tags: OpenAPI tags applied to every route.

        Returns:
            The router, ready for ``app.include_router``.
        """
        session_dep = session or self.session_dependency
        api = APIRouter(tags=list(tags))
        self._add_consent_routes(api, subject, session_dep)
        self._add_export_route(api, subject, session_dep)
        self._add_erasure_route(api, subject, session_dep)
        if restriction:
            self._add_restriction_routes(api, subject, session_dep)
        return api

    def lifespan(
        self, *, poll_interval: float = 5.0
    ) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
        """Build a FastAPI lifespan that drains the outbox in the background.

        Starts a :class:`~effaced_fastapi.SagaWorker` (a daemon thread —
        never a task on the serving loop, ADR 0006) on startup and stops
        it on shutdown. FastAPI accepts a single lifespan: if your app
        already has one, construct the worker inside it instead.

        Args:
            poll_interval: Seconds the worker sleeps when the outbox
                comes back empty.

        Returns:
            A lifespan context manager for ``FastAPI(lifespan=...)``.
        """
        runner = self._stack.saga_runner

        @asynccontextmanager
        async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
            worker = SagaWorker(runner, poll_interval=poll_interval)
            worker.start()
            try:
                yield
            finally:
                worker.stop()

        return _lifespan

    def _add_consent_routes(
        self,
        api: APIRouter,
        provider: SubjectProvider,
        session_dep: Callable[..., Iterator[Session]],
    ) -> None:
        """Mount ``POST /consent`` and ``GET /consent/{purpose}``."""
        consent = self._stack.consent

        @api.post("/consent")
        def record_consent(
            request: ConsentRequest,
            subject: Subject = Depends(provider),
            session: Session = Depends(session_dep),
        ) -> ConsentRecord:
            """Record a consent grant or withdrawal (Art. 7) — same call for both."""
            record = ConsentRecord(
                subject_id=subject.subject_id,
                purpose=request.purpose,
                policy_version=request.policy_version,
                granted=request.granted,
                recorded_at=datetime.now(UTC),
                source=SOURCE,
            )
            consent.record(session, record)
            return record

        @api.get("/consent/{purpose}")
        def consent_status(
            purpose: str,
            subject: Subject = Depends(provider),
            session: Session = Depends(session_dep),
        ) -> bool:
            """Whether the subject's latest consent event for the purpose is a grant."""
            return consent.status(session, subject.subject_id, purpose)

    def _add_export_route(
        self,
        api: APIRouter,
        provider: SubjectProvider,
        session_dep: Callable[..., Iterator[Session]],
    ) -> None:
        """Mount ``GET /export``."""
        exporter = self._stack.exporter

        @api.get("/export")
        def export_subject(
            subject: Subject = Depends(provider),
            session: Session = Depends(session_dep),
        ) -> ExportBundle:
            """Export the subject's data (Art. 15) across the database and resolvers."""
            return exporter.export_subject(session, subject.subject_id, refs=subject.refs)

    def _add_erasure_route(
        self,
        api: APIRouter,
        provider: SubjectProvider,
        session_dep: Callable[..., Iterator[Session]],
    ) -> None:
        """Mount ``DELETE`` at the router root (the include prefix)."""
        planner = self._stack.planner

        @api.delete("")
        def erase_subject(
            subject: Subject = Depends(provider),
            session: Session = Depends(session_dep),
        ) -> ErasureResult:
            """Erase the subject (Art. 17): atomic locally, saga-enqueued externally."""
            return planner.erase_subject(session, subject.subject_id, refs=subject.refs)

    def _add_restriction_routes(
        self,
        api: APIRouter,
        provider: SubjectProvider,
        session_dep: Callable[..., Iterator[Session]],
    ) -> None:
        """Mount ``POST /restriction`` and ``GET /restriction``."""
        ledger = self._stack.restriction

        @api.post("/restriction")
        def record_restriction(
            request: RestrictionRequest,
            subject: Subject = Depends(provider),
            session: Session = Depends(session_dep),
        ) -> RestrictionRecord:
            """Record a restriction placement or lift (Art. 18) — flag, not enforcement."""
            record = RestrictionRecord(
                subject_id=subject.subject_id,
                purpose=request.purpose,
                restricted=request.restricted,
                reason=request.reason,
                recorded_at=datetime.now(UTC),
                source=SOURCE,
            )
            ledger.record(session, record)
            return record

        @api.get("/restriction")
        def restriction_status(
            purpose: str | None = None,
            subject: Subject = Depends(provider),
            session: Session = Depends(session_dep),
        ) -> bool:
            """Whether a restriction currently applies (globally, or for ``purpose``)."""
            return ledger.status(session, subject.subject_id, purpose)
