"""The :class:`SupabasePostgrestResolver` — a subject's PII in PostgREST tables."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from effaced.exceptions import ConfigurationError
from effaced.resolvers import ResolverErasure, ResolverExport
from effaced_supabase.errors import raise_for_taxonomy
from effaced_supabase.postgrest_covered_surface import covered_surface_for
from effaced_supabase.postgrest_export_records import row_records

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from effaced import CoveredSurface, ExportRecord
    from effaced.annotations import SubjectRef
    from effaced_supabase.postgrest_table import PostgrestTable

_DEFAULT_TIMEOUT = 10.0
_SYSTEM = "supabase postgrest"
_RETURN_REPRESENTATION = {"Prefer": "return=representation"}


class SupabasePostgrestResolver:
    """Exports and erases a subject's PII held in PostgREST-exposed tables.

    Expects refs of kind ``"supabase_postgrest"`` (refs are routed to the
    resolver whose name equals their kind — ADR 0008) whose value is the
    subject id. The resolver is configured with an explicit list of
    :class:`~effaced_supabase.postgrest_table.PostgrestTable` declarations
    — the tables and columns holding the subject's PII and the column that
    carries the subject id. It performs **no schema discovery**: a table
    or column not declared is neither exported nor erased.

    For each table, export issues
    ``GET /rest/v1/{table}?{subject_column}=eq.{id}&select=...`` and emits
    one record per populated declared column; erasure issues
    ``DELETE /rest/v1/{table}?{subject_column}=eq.{id}`` with
    ``Prefer: return=representation``. PostgREST answers a no-match delete
    with an empty representation (not a 404); a subject whose every
    declared table deletes nothing yields ``already_absent=True`` —
    success, never an error.

    Talks to the data API with the service-role key, which bypasses
    row-level security — server-side use only. A fresh ``httpx`` client is
    built per call; nothing loop- or connection-bound is cached on the
    instance (ADR 0006). Error taxonomy is shared with the other Supabase
    resolvers (see :mod:`effaced_supabase.errors`): 4xx other than 429
    raise :class:`~effaced.exceptions.ResolverError`; rate limits, 5xx,
    and connection faults propagate so the saga runner retries.
    """

    def __init__(
        self,
        base_url: str,
        service_role_key: str,
        tables: Sequence[PostgrestTable],
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """Wire the resolver to a Supabase project's data API.

        Args:
            base_url: The project's API origin, e.g.
                ``https://<project-ref>.supabase.co`` (or a self-hosted
                origin).
            service_role_key: The service-role key. PostgREST applies it
                with RLS bypassed; treat it as a root credential and never
                ship it client-side.
            tables: The declared PII-bearing tables, at least one. Empty
                configuration raises :class:`~effaced.exceptions.ConfigurationError`
                — a resolver that reaches nothing is a wiring mistake, not
                a degraded mode.
            transport: Optional transport override; tests inject a fake
                here so no call ever leaves the process.
            timeout: Per-request timeout in seconds.

        Raises:
            ConfigurationError: No tables were declared.
        """
        if not tables:
            msg = "SupabasePostgrestResolver needs at least one declared table"
            raise ConfigurationError(msg)
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {service_role_key}",
            "apikey": service_role_key,
        }
        self._tables = tuple(tables)
        self._transport = transport
        self._timeout = timeout
        self._covered_surface = covered_surface_for(self._tables)

    @property
    def name(self) -> str:
        """Stable resolver name recorded in manifests and audits."""
        return "supabase_postgrest"

    @property
    def covered_surface(self) -> CoveredSurface:
        """The declared PII this resolver reaches (:class:`~effaced.AttestingResolver`).

        Returns:
            A :class:`~effaced.CoveredSurface` with one field per declared
            column, built from the same table list the exporter walks so
            the declaration and the export cannot drift.
        """
        return self._covered_surface

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Collect the subject's PII across the declared tables (Art. 15).

        Args:
            ref: ``kind="supabase_postgrest"``, ``value=<subject id>``.

        Returns:
            One record per populated declared column of every matching
            row, sourced under each table's name; empty when no declared
            table holds the subject.

        Raises:
            ResolverError: The key is invalid, lacks access, a declared
                table is missing, or the request was malformed — retrying
                cannot succeed.
        """
        records: list[ExportRecord] = []
        for table in self._tables:
            rows = await asyncio.to_thread(self._select, table, ref.value)
            for row in rows:
                records.extend(row_records(table, row))
        return ResolverExport(resolver=self.name, records=tuple(records))

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Delete the subject's rows from the declared tables (Art. 17).

        Args:
            ref: ``kind="supabase_postgrest"``, ``value=<subject id>``.

        Returns:
            The outcome; ``already_absent=True`` when no declared table
            held a row for the subject.

        Raises:
            ResolverError: The key is invalid, lacks access, a declared
                table is missing, or the request was malformed — retrying
                cannot succeed.
        """
        deleted_any = False
        for table in self._tables:
            deleted = await asyncio.to_thread(self._delete, table, ref.value)
            deleted_any = deleted_any or bool(deleted)
        if not deleted_any:
            return ResolverErasure(
                resolver=self.name,
                already_absent=True,
                detail="subject already absent in supabase postgrest",
            )
        return ResolverErasure(resolver=self.name, detail="subject deleted in supabase postgrest")

    def _select(self, table: PostgrestTable, subject_id: str) -> list[object]:
        """Read the subject's rows from one table (runs in a worker thread)."""
        params = {
            table.subject_column: f"eq.{subject_id}",
            "select": ",".join(column.name for column in table.columns),
        }
        response = self._request("GET", table.name, params=params)
        raise_for_taxonomy(response, "export", system=_SYSTEM)
        return _json_rows(response)

    def _delete(self, table: PostgrestTable, subject_id: str) -> list[object]:
        """Delete the subject's rows from one table; returns the deleted rows."""
        params = {table.subject_column: f"eq.{subject_id}"}
        response = self._request(
            "DELETE", table.name, params=params, headers=_RETURN_REPRESENTATION
        )
        raise_for_taxonomy(response, "erasure", system=_SYSTEM)
        return _json_rows(response)

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """One data-API call on a per-call client (runs in a worker thread).

        The table name is percent-encoded into a single path segment and
        the subject id rides in ``params`` so ``httpx`` URL-encodes it —
        a raw ``/`` or operator character in either can never reshape the
        request into a different table or a wider match.
        """
        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            return client.request(
                method,
                f"/rest/v1/{quote(table, safe='')}",
                params=params,
                headers=headers,
            )


def _json_rows(response: httpx.Response) -> list[object]:
    """The response body as a list of rows; ``[]`` for any non-array body."""
    payload: object = response.json()
    if isinstance(payload, list):
        return list(payload)
    return []
