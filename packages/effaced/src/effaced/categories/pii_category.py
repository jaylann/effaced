"""The :class:`PiiCategory` vocabulary."""

from __future__ import annotations

from enum import StrEnum


class PiiCategory(StrEnum):
    """What kind of personal data a field holds.

    Categories drive export grouping (Art. 15 bundles are organised by
    category) and are recorded in the audit trail. Adding members is a
    MINOR change; removing or renaming members is a MAJOR change.
    """

    CONTACT = "contact"
    """Email addresses, phone numbers, postal addresses."""

    IDENTITY = "identity"
    """Names, usernames, government identifiers, birth dates."""

    FINANCIAL = "financial"
    """Payment details, invoices, billing references."""

    BEHAVIORAL = "behavioral"
    """Usage history, preferences, interaction logs."""

    TECHNICAL = "technical"
    """IP addresses, device identifiers, cookies, user agents."""

    LOCATION = "location"
    """Geolocation data, time zones tied to a person."""

    COMMUNICATION = "communication"
    """Message bodies, support tickets, user-generated content."""

    SPECIAL = "special"
    """Art. 9 special categories (health, beliefs, biometrics). Handle with care."""
