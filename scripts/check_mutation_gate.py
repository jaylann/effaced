r"""Fail when mutation testing surfaces an un-accounted-for surviving mutant.

A surviving mutant is a behaviour the test suite executes but does not pin:
mutmut changed the code and no test failed (see ``.claude/rules/testing.md``).
This gate turns the weekly ``deep-checks`` mutation job from report-only into a
hard gate on the erasure-critical modules (``audit``/``consent``/``erasure``/
``manifest``/``saga``), holding the survivor set at a named *equivalent-mutant
floor*: the allowlist in ``packages/effaced/mutation-equivalents.txt``.

The gate reads ``mutmut results`` output (on stdin, or by shelling out) and:

* fails when any ``survived`` or ``no tests`` mutant is **not** allowlisted —
  a newly-unpinned behaviour;
* fails when an allowlist entry did **not** appear as a survivor — a stale
  exemption that silently widens the floor (the same discipline as
  ``assert_data_map_complete``'s stale-exemption failure);
* warns (does not fail) on ``timeout``/``suspicious`` mutants — non-deterministic
  signals that a hard gate would only flake on.

Run from the repo root or with ``mutmut results`` piped in::

    cd packages/effaced && uv run mutmut results | \\
        uv run python ../../scripts/check_mutation_gate.py

Keeping the logic in small pure functions (``parse_results``, ``decide``) lets
the unit tests in ``packages/effaced/tests/test_mutation_gate.py`` exercise the
decision without invoking mutmut.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# Statuses that mean "this behaviour is not pinned by a test" — the gate fails
# on any of these that is not on the allowlist.
FAILING_STATUSES = frozenset({"survived", "no tests"})
# Non-deterministic statuses: reported as warnings, never gate failures.
WARNING_STATUSES = frozenset({"timeout", "suspicious"})

_ALLOWLIST = Path("packages/effaced/mutation-equivalents.txt")


@dataclass(frozen=True)
class GateDecision:
    """The outcome of comparing survivors against the allowlist.

    Attributes:
        unexpected: Failing-status mutants absent from the allowlist —
            newly unpinned behaviours that must get a killing test (or, if
            provably equivalent, an allowlist entry with its argument).
        stale: Allowlist entries that did not appear as a survivor this run
            — exemptions that no longer correspond to a real mutant and so
            silently widen the floor. Stale entries fail the gate loudly.
        warnings: Mutants with a non-deterministic status, reported only.
    """

    unexpected: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the gate passes (no unexpected survivors, no stale entries)."""
        return not self.unexpected and not self.stale


@dataclass
class _Survivors:
    """Mutants grouped by how the gate treats their status."""

    failing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_results(text: str) -> _Survivors:
    """Parse ``mutmut results`` output into gate-relevant survivor groups.

    ``mutmut results`` prints one line per non-killed mutant, formatted as
    ``<dotted.mutant.name>: <status>`` (leading whitespace and section
    headers are ignored). Lines without a recognised ``": "`` status
    separator — banners, blank lines, progress spinners — are skipped.

    Args:
        text: The raw stdout of ``mutmut results``.

    Returns:
        The failing-status and warning-status mutant names.
    """
    survivors = _Survivors()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ": " not in line:
            continue
        name, _, status = line.rpartition(": ")
        status = status.strip().lower()
        name = name.strip()
        if not name:
            continue
        if status in FAILING_STATUSES:
            survivors.failing.append(name)
        elif status in WARNING_STATUSES:
            survivors.warnings.append(name)
    return survivors


def parse_allowlist(text: str) -> tuple[str, ...]:
    """Parse the equivalent-mutant allowlist file.

    One mutant name per line; ``#`` comments (whole-line or trailing) and
    blank lines are ignored.

    Args:
        text: The contents of ``mutation-equivalents.txt``.

    Returns:
        The allowlisted mutant names, de-duplicated, in first-seen order.
    """
    names: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and line not in seen:
            seen.add(line)
            names.append(line)
    return tuple(names)


def decide(survivors: _Survivors, allowlist: tuple[str, ...]) -> GateDecision:
    """Compare survivors against the allowlist into a pass/fail decision.

    Args:
        survivors: Parsed ``mutmut results`` survivor groups.
        allowlist: Allowlisted equivalent-mutant names.

    Returns:
        The :class:`GateDecision`: unexpected survivors, stale allowlist
        entries, and warnings.
    """
    allowed = set(allowlist)
    failing = set(survivors.failing)
    unexpected = tuple(name for name in survivors.failing if name not in allowed)
    stale = tuple(name for name in allowlist if name not in failing)
    return GateDecision(
        unexpected=unexpected,
        stale=stale,
        warnings=tuple(survivors.warnings),
    )


def render(decision: GateDecision) -> str:
    """Render a human-readable report of a gate decision."""
    lines: list[str] = []
    if decision.warnings:
        lines.append("warning: non-deterministic mutants (not gated):")
        lines.extend(f"  {name}" for name in decision.warnings)
    if decision.unexpected:
        lines.append("FAIL: surviving mutants not on the equivalent-mutant allowlist:")
        lines.extend(f"  {name}" for name in decision.unexpected)
        lines.append(
            "  → write a killing test, or (if provably equivalent) add it to "
            f"{_ALLOWLIST} with the equivalence argument."
        )
    if decision.stale:
        lines.append("FAIL: stale allowlist entries (no longer a surviving mutant):")
        lines.extend(f"  {name}" for name in decision.stale)
        lines.append(f"  → remove them from {_ALLOWLIST}; the floor must track reality.")
    if decision.ok:
        lines.append("✓ mutation gate green — survivors are exactly the allowlisted floor")
    return "\n".join(lines)


def main() -> int:
    """Run the mutation gate on piped ``mutmut results`` output.

    Reads the results from stdin (``uv run mutmut results | uv run python
    scripts/check_mutation_gate.py``) — there is deliberately no shell-out
    fallback; the caller controls which ``mutants/`` tree is judged.

    Returns:
        ``0`` when survivors match the allowlist exactly, ``1`` otherwise
        (including when nothing was piped in).
    """
    if sys.stdin.isatty():
        print("usage: pipe `mutmut results` output into this script", file=sys.stderr)
        return 1
    allowlist_path = Path(__file__).resolve().parent.parent / _ALLOWLIST
    allowlist = parse_allowlist(allowlist_path.read_text(encoding="utf-8"))
    survivors = parse_results(sys.stdin.read())
    decision = decide(survivors, allowlist)
    print(render(decision))
    return 0 if decision.ok else 1


if __name__ == "__main__":
    sys.exit(main())
