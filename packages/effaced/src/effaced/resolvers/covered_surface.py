"""The :class:`CoveredSurface` — a resolver's claimed reach, made testable."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced.resolvers.covered_field import CoveredField
from effaced.resolvers.surface_exclusion import SurfaceExclusion


class CoveredSurface(BaseModel):
    """The PII a resolver claims to reach, declared and conformance-tested.

    A covered surface is one resolver's explicit answer to "which
    PII-bearing fields in the external system does this resolver's export
    and erasure actually reach, and which does it knowingly not reach?".
    It pairs the covered fields (:class:`~effaced.CoveredField` globs with
    categories) with the explicit :class:`~effaced.SurfaceExclusion`
    gaps, plus free-text ``notes`` for asymmetries that are not a
    per-field exclusion — for example that the S3 resolver exports the
    *current* object versions but erases *all* versions and delete
    markers.

    **Boundary.** This makes the resolver's *claimed* coverage explicit
    and testable: the conformance suite checks every exported record of a
    present subject matches a covered field of the same category (subset),
    no record matches an exclusion (absence), and — for a fully-populated
    fixture — every covered field is exercised (enumeration). It can
    **never** prove the external system holds no personal data the
    resolver does not reach; coverage of an opaque third-party system is
    unknowable from the outside. It is a mechanism for declaring reach,
    never a compliance determination.

    Attributes:
        resolver: The :attr:`Resolver.name <effaced.Resolver.name>` this
            surface describes; the conformance suite checks it equals the
            resolver under test.
        fields: The covered fields — at least one. Each is a glob over
            :attr:`ExportRecord.field <effaced.ExportRecord.field>` with
            the category the matched field is declared to hold.
        exclusions: Fields the resolver explicitly does not reach, each
            with a human reason; no exported record may match one.
        notes: Free-text caveats and asymmetries that are not a per-field
            exclusion — read alongside the fields, never machine-checked.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver: str = Field(min_length=1)
    fields: tuple[CoveredField, ...] = Field(min_length=1)
    exclusions: tuple[SurfaceExclusion, ...] = ()
    notes: tuple[str, ...] = ()
