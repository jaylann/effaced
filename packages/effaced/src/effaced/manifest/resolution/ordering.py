"""FK-safe deletion ordering — a pure topological sort over table names."""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING

from effaced.exceptions import SubjectResolutionError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def fk_safe_deletion_order(
    tables: Sequence[str],
    foreign_keys: Iterable[tuple[str, str]],
) -> tuple[str, ...]:
    """Order tables so deleting in sequence never violates a foreign key.

    Children come before their parents: a ``(child, parent)`` edge means
    ``child`` holds a foreign key referencing ``parent``, so the child's
    rows must go first. Self-referential edges are ignored — one ``DELETE``
    statement removes a table's whole subject-row set, and foreign keys are
    checked per statement, so parent and child rows within one table die
    together. The result is deterministic for a given input order.

    Args:
        tables: The table names to order, in a stable caller-chosen order.
        foreign_keys: ``(child, parent)`` edges between those tables.

    Returns:
        The tables in FK-safe deletion order.

    Raises:
        SubjectResolutionError: If an edge references a table outside
            ``tables``, or the edges form a cross-table cycle (no safe
            deletion order exists).
    """
    known = set(tables)
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for table in tables:
        sorter.add(table)
    for child, parent in foreign_keys:
        if child not in known or parent not in known:
            msg = f"foreign-key edge {child!r} -> {parent!r} references a table outside the graph"
            raise SubjectResolutionError(msg)
        if child != parent:
            sorter.add(parent, child)
    try:
        return tuple(sorter.static_order())
    except CycleError as exc:
        cycle: list[str] = exc.args[1]
        msg = f"foreign-key cycle prevents a safe deletion order: {' -> '.join(cycle)}"
        raise SubjectResolutionError(msg) from exc
