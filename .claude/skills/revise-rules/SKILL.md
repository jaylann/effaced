---
name: revise-rules
description: Freshness audit of all .claude/rules/*.md and CLAUDE.md files — verify referenced commands/paths/APIs still exist; fix or flag stale guidance.
---

# /revise-rules — keep the guidance true

Stale guidance is a bug. This skill audits every instruction file against the current codebase.

1. **Inventory**: `CLAUDE.md`, `packages/*/CLAUDE.md`, every `.claude/rules/*.md`, `.claude/skills/*/SKILL.md`, agent definitions.
2. **Verify each concrete claim**:
   - Referenced paths/files exist (`packages/effaced/src/effaced/...`).
   - Referenced commands work (`just <recipe>` exists in the justfile; flags valid).
   - Referenced APIs exist with the documented shape (class names, signatures, enum members).
   - `paths:` frontmatter globs still match real files.
   - Counts/claims ("600-line cap", tool lists) match `pyproject.toml`/`scripts/` reality.
3. **Fix in place** what is unambiguously stale (renamed module, moved file, changed flag). For judgment calls (a rule that may be intentionally aspirational), list them for the user instead of editing.
4. **Distill** any `## Learnings` entries in CLAUDE.md into the matching rule file and clear the section (same as /commit step 2).
5. **Report**: files audited, fixes applied (with one-line reasons), open questions.
