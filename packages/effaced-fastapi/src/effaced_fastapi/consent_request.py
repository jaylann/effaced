"""The :class:`ConsentRequest` body of the consent endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConsentRequest(BaseModel):
    """What the caller decides about consent — nothing more.

    The server fills in everything the caller must not control: the
    subject id comes from the authenticated :class:`~effaced_fastapi.Subject`,
    the timestamp from the server clock, and the source is fixed to
    ``"api"``. The same body records a grant (``granted=True``) and a
    withdrawal (``granted=False``) — one call, per Art. 7(3)'s "as easy to
    withdraw as to give".

    Attributes:
        purpose: The processing purpose consented to (e.g. ``"newsletter"``).
        granted: ``True`` for a grant, ``False`` for a withdrawal.
        policy_version: Version of the policy text the subject saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    purpose: str = Field(min_length=1, max_length=255)
    granted: bool
    policy_version: str = Field(min_length=1, max_length=255)
