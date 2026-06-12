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

## Versioned docs (starlight-versions, ADR 0016)
- `stage` HEAD is the **current "Latest"** docs. Frozen per-release snapshots live under `site/src/content/docs/<slug>/docs/…` with a sidebar snapshot at `site/src/content/versions/<slug>.json`. Today there is one: `0.1` (the released 0.1.0 line). The version switcher is wired in `astro.config.mjs` via the `starlightVersions` plugin; the `versions` collection is in `site/src/content.config.ts`.
- **Snapshots are committed, not generated.** Only the *current* reference (`site/src/content/docs/docs/reference/*`) is gitignored; snapshot reference pages under `<slug>/docs/reference/` are tracked. `just site-gen` regenerates only the current reference and never touches a snapshot (`gen_api_docs.py` clears only its own `OUT_DIR`). Never hand-edit a snapshot to track `stage` — it's a release artifact.
- **Cut a new snapshot per minor/major release** (`0.N.0` / `1.0.0`), reflecting *that tag's* API, not stage HEAD. Procedure: configure the new `{ slug }` in `astro.config.mjs`; check out the release tag in a throwaway worktree and run its `scripts/gen_api_docs.py` (under its own `uv` env) to produce that version's reference; replace the site's current `docs/docs/` with the tag's hand-written pages + that generated reference and set the sidebar to the tag's; run `pnpm build` once so the plugin snapshots into `<slug>/docs/…` and writes `<slug>.json`; then restore stage HEAD's `docs/docs/` and sidebar. The plugin only snapshots a slug whose directory is still absent. Commit the snapshot.
- Custom domain (`effaced.dev`) is the still-open half of issue #49 — `SITE_URL`/`BASE_PATH` stay env-driven, untouched.

## Self-documenting rules loop (keep the docs alive)
- Non-obvious discoveries go into `## Learnings` in CLAUDE.md as you work; the `/commit` skill distills them into the matching `.claude/rules/*.md` and clears the section.
- **Stale guidance is a bug.** If a rule, CLAUDE.md, or README references a command, path, or API that no longer exists, fix it in the same PR that made it stale — or immediately when discovered.
- PRs that change public API, commands, or structure must update the matching rule/CLAUDE.md (the reviewer agent checks this).
- `/revise-rules` runs an on-demand freshness audit over all rules and CLAUDE.md files.

## Wording discipline (legal, non-negotiable)
- Never write that effaced "makes you compliant", "ensures GDPR compliance", or similar — in README, docstrings, examples, log messages, anywhere. Mechanisms, not determinations.
- Erasure/export behaviour changes get a prominent **Security** mention in changelogs (an erasure bug is a data-protection bug — say so loudly).
- README discipline: problem first, honest comparison table, explicit "not legal advice" section. Don't dilute these.
