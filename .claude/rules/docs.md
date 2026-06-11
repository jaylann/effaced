---
paths: ["**/*.md", "**/*.py", "site/**"]
---

# Documentation & wording

## Docstrings are the docs site
- Google-style docstrings on every public module/class/function (ruff `D` enforces). `scripts/gen_api_docs.py` (griffe, ADR 0011) renders them verbatim as the site's API reference — write prose, not stubs.
- Sphinx-style cross-refs (`:class:`, `:meth:`, …) become links on the site; point them at public (re-exported) symbols. The generator exits non-zero on refs it can't convert and on `__all__` names no reference page claims — a new public subpackage means a new `PageSpec` in the script.
- Document contracts, not mechanics: idempotency promises, append-only guarantees, what raises and why.

## The site (site/ — Astro Starlight, ADR 0011)
- Hand-written pages live in `site/src/content/docs/docs/`; `…/reference/` is generated and gitignored — never hand-edit it. `just site-dev` / `just site-build` regenerate it first.
- Internal MDX links are relative with trailing slashes; absolute `/docs/...` paths break under the `/effaced/` base path on GitHub Pages.
- Bare `{` or `<` in MDX prose breaks the build — keep them in code spans/fences.
- The roadmap page's content lives in `site/src/data/roadmap.ts` — shipping (or re-scoping) a roadmap item flips its entry there in the same PR. No dates on the roadmap, ever.
- The wording discipline below binds the marketing page and every docs page, not just README/docstrings.

## Self-documenting rules loop (keep the docs alive)
- Non-obvious discoveries go into `## Learnings` in CLAUDE.md as you work; the `/commit` skill distills them into the matching `.claude/rules/*.md` and clears the section.
- **Stale guidance is a bug.** If a rule, CLAUDE.md, or README references a command, path, or API that no longer exists, fix it in the same PR that made it stale — or immediately when discovered.
- PRs that change public API, commands, or structure must update the matching rule/CLAUDE.md (the reviewer agent checks this).
- `/revise-rules` runs an on-demand freshness audit over all rules and CLAUDE.md files.

## Wording discipline (legal, non-negotiable)
- Never write that effaced "makes you compliant", "ensures GDPR compliance", or similar — in README, docstrings, examples, log messages, anywhere. Mechanisms, not determinations.
- Erasure/export behaviour changes get a prominent **Security** mention in changelogs (an erasure bug is a data-protection bug — say so loudly).
- README discipline: problem first, honest comparison table, explicit "not legal advice" section. Don't dilute these.
