"""The :func:`assert_data_map_complete` CI gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.adapters.sqlalchemy.completeness_linter import lint_completeness

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy import MetaData

    from effaced.lint import CompletenessFinding


def assert_data_map_complete(
    metadata: MetaData,
    *,
    exempt_tables: Collection[str] = (),
    exempt_columns: Collection[str] = (),
) -> None:
    """Fail when the metadata holds data the data map does not cover.

    The CI companion to
    :func:`effaced.adapters.sqlalchemy.lint_completeness`: drop it into any
    test and the build fails the moment a new table or column appears
    without an annotation — undeclared data never accumulates silently.

    Exemptions are conscious, reviewable acknowledgements ("this holds no
    personal data") living in your test code. An exempt table silences all
    of its findings; an exempt column (``"table.column"``) silences just
    that field. A stale exemption — one matching no finding — fails too,
    so the list never outlives the schema it judged.

    Args:
        metadata: The ``MetaData`` holding your mapped tables (for the ORM,
            ``Base.metadata``).
        exempt_tables: Table names acknowledged as holding no personal data.
        exempt_columns: ``"table.column"`` names acknowledged likewise.

    Raises:
        AssertionError: Listing every unexempted finding and every stale
            exemption.
    """
    findings = lint_completeness(metadata)
    stale = set(exempt_tables) | set(exempt_columns)
    problems = [
        finding.message
        for finding in findings
        if not _exempt(finding, exempt_tables, exempt_columns, stale)
    ]
    problems.extend(f"exemption {name!r} matches nothing — remove it" for name in sorted(stale))
    if problems:
        details = "\n  - ".join(problems)
        msg = f"the data map does not cover this metadata:\n  - {details}"
        raise AssertionError(msg)


def _exempt(
    finding: CompletenessFinding,
    exempt_tables: Collection[str],
    exempt_columns: Collection[str],
    stale: set[str],
) -> bool:
    """Whether the finding is exempted; marks used exemptions as not stale."""
    if finding.table in exempt_tables:
        stale.discard(finding.table)
        return True
    if finding.column is not None:
        qualified = f"{finding.table}.{finding.column}"
        if qualified in exempt_columns:
            stale.discard(qualified)
            return True
    return False
