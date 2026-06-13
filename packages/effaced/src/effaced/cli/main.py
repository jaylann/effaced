"""The ``effaced lint`` command — run the linters from the shell or CI."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from effaced.adapters.sqlalchemy import (
    collect_data_map,
    lint_completeness,
    lint_reachability,
    load_lint_target,
)
from effaced.exceptions import ConfigurationError, ManifestError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from effaced.adapters.sqlalchemy import LintTarget
    from effaced.lint import CompletenessFinding, ReachabilityFinding

_FRAMING = (
    "Findings are questions, not verdicts: each names data the manifest does "
    "not cover or cannot reach, for you to annotate or consciously exempt. "
    "effaced ships mechanisms, never determinations."
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``effaced`` CLI and return its process exit code.

    Args:
        argv: Argument vector excluding the program name; defaults to
            ``sys.argv[1:]`` when ``None``.

    Returns:
        ``0`` when no findings, ``1`` when the linters report at least one
        finding, ``2`` on a usage or load error (a malformed target spec, an
        un-importable module, or malformed annotations). Findings are written
        to stdout; errors to stderr.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "lint":
        return _run_lint(str(args.target))
    parser.print_help(sys.stderr)  # pragma: no cover - argparse exits on no subcommand
    return 2  # pragma: no cover


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser (``lint <target>``)."""
    parser = argparse.ArgumentParser(
        prog="effaced",
        description="Mechanisms for GDPR data-subject requests. Lint your data map.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    lint = subparsers.add_parser(
        "lint",
        help="report data the manifest does not cover or the planner cannot reach",
        description=(
            "Lint a declarative Base or MetaData for completeness and erasure-plan reachability."
        ),
    )
    lint.add_argument(
        "target",
        help="import path of your declarative Base or MetaData, e.g. 'myapp.models:Base'",
    )
    return parser


def _run_lint(target_spec: str) -> int:
    """Load the target, lint it, print findings, and return the exit code."""
    try:
        target = load_lint_target(target_spec)
        completeness = lint_completeness(target.metadata)
        reachability = _reachability(target)
    except (ConfigurationError, ManifestError) as exc:
        print(f"effaced lint: {exc}", file=sys.stderr)
        return 2
    return _report(completeness, reachability, registry_present=target.orm_registry is not None)


def _reachability(target: LintTarget) -> tuple[ReachabilityFinding, ...]:
    """Reachability findings, or none when the target carries no registry."""
    if target.orm_registry is None:
        return ()
    data_map = collect_data_map(target.metadata)
    return lint_reachability(data_map, target.orm_registry)


def _report(
    completeness: tuple[CompletenessFinding, ...],
    reachability: tuple[ReachabilityFinding, ...],
    *,
    registry_present: bool,
) -> int:
    """Print findings and the framing line; return 0 (clean) or 1 (findings)."""
    for completeness_finding in completeness:
        print(completeness_finding.message)
    for reachability_finding in reachability:
        print(reachability_finding.message)
    if not registry_present:
        print("note: no ORM registry on the target — reachability linting was skipped")
    total = len(completeness) + len(reachability)
    if total == 0:
        print("no findings: every store is covered and reachable")
        return 0
    print(f"{total} finding(s). {_FRAMING}")
    return 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
