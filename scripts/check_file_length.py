"""Fail when a source file outgrows the architecture budget.

Small, searchable files are a hard rule here (see .claude/rules/python.md):
one concept per file, 600 lines max. This gate runs in `just check` and CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_LINES = 600
SRC_GLOBS = ("packages/*/src/**/*.py",)


def main() -> int:
    """Scan source trees; report every file over budget."""
    root = Path(__file__).resolve().parent.parent
    offenders: list[tuple[Path, int]] = []
    for pattern in SRC_GLOBS:
        for path in sorted(root.glob(pattern)):
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > MAX_LINES:
                offenders.append((path.relative_to(root), lines))
    for path, lines in offenders:
        print(f"{path}: {lines} lines (max {MAX_LINES}) — split it; one concept per file")
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
