"""The :class:`PiiSpec` model — one field's full personal-data declaration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from effaced.annotations.retention_policy import RetentionPolicy
from effaced.categories import ErasureStrategy, LegalBasis, PiiCategory


class PiiSpec(BaseModel):
    """Full declaration for one personal-data field.

    Built by the adapter authoring helpers (e.g.
    :func:`effaced.adapters.sqlalchemy.pii`); read back by
    :class:`effaced.manifest.DataMap`.

    Attributes:
        category: What kind of personal data this is.
        erasure: What happens on Art. 17 erasure. Defaults to ``DELETE``.
        retention: Required when ``erasure`` is ``RETAIN`` (and allowed with
            ``ANONYMIZE`` to document why the record itself survives).
        legal_basis: Why the data is processed at all (Art. 15 metadata).
        purpose: Processing purpose, surfaced verbatim in export bundles.
        description: Optional human note for audits and the PII linter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: PiiCategory
    erasure: ErasureStrategy = ErasureStrategy.DELETE
    retention: RetentionPolicy | None = None
    legal_basis: LegalBasis | None = None
    purpose: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _retain_requires_policy(self) -> PiiSpec:
        """A retention duty must name its legal reason."""
        if self.erasure is ErasureStrategy.RETAIN and self.retention is None:
            msg = (
                "ErasureStrategy.RETAIN requires a RetentionPolicy — "
                "a retention duty must name its legal reason."
            )
            raise ValueError(msg)
        return self
