---
name: commit
description: Full commit cycle — simplify, distill learnings into rules, run checks and tests, then commit with Conventional Commits + DCO sign-off.
---

# /commit — the full commit cycle

Run these steps in order. If the user said "just commit" / "commit so far", skip to step 5 directly (no simplify, no tests) — do exactly what they asked.

1. **Simplify** — run `code-simplifier:code-simplifier` on the files changed in this session.
2. **Distill learnings** — run `claude-md-management:revise-claude-md`: migrate `## Learnings` entries from `CLAUDE.md` into the matching `.claude/rules/*.md` (or the per-package CLAUDE.md), then clear the section. While there, fix any rule/doc the session revealed to be stale — stale guidance is a bug.
3. **Checks** — `just check` (ruff lint + format check + mypy strict + file-length gate). Fix failures; don't suppress.
4. **Tests** — `just test`. If erasure/export semantics were touched, confirm bleed/retention proofs exist.
5. **Stage & commit**
   - Stage only files you touched (plus rule files revised in step 2). Never `git add -A` blindly.
   - Message: Conventional Commits, lowercase subject ≤72 chars.
   - **Always `git commit -s`** (DCO). Never add Co-Authored-By trailers.
6. **Issues** — if the work closes a GitHub issue and the fix is verified, close it with a brief comment + commit ref. If partially done, comment findings instead. Unrelated discoveries → new issue.
