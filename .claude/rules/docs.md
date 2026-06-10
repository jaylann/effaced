---
paths: ["**/*.md", "**/*.py"]
---

# Documentation & wording

## Docstrings are the docs site
- Google-style docstrings on every public module/class/function (ruff `D` enforces). A docs generator (likely MkDocs Material + mkdocstrings, see ADR 0005) will render these verbatim — write prose, not stubs.
- Document contracts, not mechanics: idempotency promises, append-only guarantees, what raises and why.

## Self-documenting rules loop (keep the docs alive)
- Non-obvious discoveries go into `## Learnings` in CLAUDE.md as you work; the `/commit` skill distills them into the matching `.claude/rules/*.md` and clears the section.
- **Stale guidance is a bug.** If a rule, CLAUDE.md, or README references a command, path, or API that no longer exists, fix it in the same PR that made it stale — or immediately when discovered.
- PRs that change public API, commands, or structure must update the matching rule/CLAUDE.md (the reviewer agent checks this).
- `/revise-rules` runs an on-demand freshness audit over all rules and CLAUDE.md files.

## Wording discipline (legal, non-negotiable)
- Never write that effaced "makes you compliant", "ensures GDPR compliance", or similar — in README, docstrings, examples, log messages, anywhere. Mechanisms, not determinations.
- Erasure/export behaviour changes get a prominent **Security** mention in changelogs (an erasure bug is a data-protection bug — say so loudly).
- README discipline: problem first, honest comparison table, explicit "not legal advice" section. Don't dilute these.
