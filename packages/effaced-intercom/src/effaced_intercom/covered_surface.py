"""The :class:`~effaced.CoveredSurface` :data:`INTERCOM_COVERED_SURFACE` declares.

The covered fields are built from the ``_CONTACT_FIELDS`` and
``_CONVERSATION_FIELDS`` tuples in :mod:`effaced_intercom.export_records`
— the same tuples the exporter iterates to emit its records — so
declaration and exporter cannot drift by construction. Conversation ids
are dynamic, so each conversation field is declared as a glob
(``conversation.*.created_at``) the exporter's concrete
``conversation.{id}.created_at`` rows match.

This is a declaration of *claimed* reach, never a compliance
determination.
"""

from __future__ import annotations

from effaced import CoveredField, CoveredSurface, SurfaceExclusion
from effaced_intercom.export_records import _CONTACT_FIELDS, _CONVERSATION_FIELDS

_CONTACT_COVERED: tuple[CoveredField, ...] = tuple(
    CoveredField(field=f"contact.{key}", category=category) for key, category in _CONTACT_FIELDS
)

_CONVERSATION_COVERED: tuple[CoveredField, ...] = tuple(
    CoveredField(field=f"conversation.*.{key}", category=category)
    for key, category in _CONVERSATION_FIELDS
)

INTERCOM_COVERED_SURFACE = CoveredSurface(
    resolver="intercom",
    fields=(*_CONTACT_COVERED, *_CONVERSATION_COVERED),
    exclusions=(
        SurfaceExclusion(
            field="contact.custom_attributes.*",
            reason="The contact custom-attributes blob is caller-defined and "
            "unknowable; PII pushed there belongs to the application's own "
            "data map.",
        ),
        SurfaceExclusion(
            field="conversation.*.conversation_parts.*",
            reason="Conversation message bodies and replies are deep "
            "COMMUNICATION content this resolver deliberately never exports; "
            "only interaction metadata (timestamps, state) is collected.",
        ),
    ),
    notes=(
        "Erasure deletes the Intercom contact by id (DELETE /contacts/{id}); "
        "conversation records are not separately deleted by this resolver — "
        "their retention is Intercom's, documented in the README.",
    ),
)
"""Intercom's declared covered surface; see :class:`~effaced.CoveredSurface`."""
