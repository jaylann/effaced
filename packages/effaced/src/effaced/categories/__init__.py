"""Vocabulary for classifying personal data and its legal context.

These enums are part of the manifest format. Adding members is a MINOR
change; removing or renaming members is a MAJOR change (it alters what
existing manifests mean — see the widened SemVer policy in CONTRIBUTING).
"""

from effaced.categories.erasure_strategy import ErasureStrategy
from effaced.categories.legal_basis import LegalBasis
from effaced.categories.pii_category import PiiCategory

__all__ = ["ErasureStrategy", "LegalBasis", "PiiCategory"]
