"""effaced-fastapi — mount effaced's data-subject endpoints on a FastAPI app.

Wires the full :class:`effaced.EffacedStack` from your annotated
declarative base and exposes the trigger points — consent (Art. 7),
export (Art. 15), erasure (Art. 17), and optionally restriction
(Art. 18) — as one :class:`fastapi.APIRouter`. Your auth stays yours: a
dependency you provide resolves the request's :class:`Subject`.

The endpoints call the same engines, under the same audit and
idempotency contracts, that you could wire by hand — this package only
removes the mechanical wiring.
"""

from effaced_fastapi.consent_request import ConsentRequest
from effaced_fastapi.integration import EffacedFastAPI
from effaced_fastapi.restriction_request import RestrictionRequest
from effaced_fastapi.saga_worker import SagaWorker
from effaced_fastapi.subject import Subject, SubjectProvider

__all__ = [
    "ConsentRequest",
    "EffacedFastAPI",
    "RestrictionRequest",
    "SagaWorker",
    "Subject",
    "SubjectProvider",
]
