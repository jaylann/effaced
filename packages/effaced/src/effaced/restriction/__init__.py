"""Art. 18 restriction of processing — a queryable, audited flag, never enforcement.

effaced ships Recital 67's "clearly indicated in the system": an append-only
ledger of restriction events and a derived :meth:`RestrictionLedger.status`
your application consults before processing. Which processing must stop — and whether an
operation falls under an Art. 18(2) exception — is a determination only the
controller can make; nothing here intercepts queries or claims compliance.
"""

from effaced.restriction.ledger import RestrictionLedger
from effaced.restriction.record import RestrictionRecord

__all__ = ["RestrictionLedger", "RestrictionRecord"]
