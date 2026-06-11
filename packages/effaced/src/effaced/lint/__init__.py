"""Completeness linting — make undeclared data visible, never guessed at.

effaced cannot find data you never declared; what it can do is point at
every place such data could hide. Findings are questions for a human
("is this personal data?"), not determinations — annotate the column or
exempt it consciously, in reviewable code.

Adapters provide the linters (see
:func:`effaced.adapters.sqlalchemy.lint_completeness`); this package holds
the storage-agnostic finding model they emit.
"""

from effaced.lint.completeness_finding import CompletenessFinding

__all__ = ["CompletenessFinding"]
