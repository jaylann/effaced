# effaced

GDPR data-subject mechanisms — Art. 15 export, Art. 17 erasure, Art. 7 consent, append-only audit — across the user's own database and external systems (resolvers; Stripe, Supabase, and S3 first). uv workspace monorepo: `packages/effaced` (core) + `packages/effaced-stripe` + `packages/effaced-supabase` + `packages/effaced-s3`, plus `site/` (Astro Starlight docs + marketing, pnpm, outside the uv workspace — ADR 0011). **We ship mechanisms, never compliance determinations.**

## Read these first

Rules auto-load by `paths:` frontmatter; when you touch X, the matching rule is binding.

| You touch | Read |
|---|---|
| Any `.py` | `.claude/rules/python.md` (strict typing, pydantic-first, one-concept-per-file, 600-line cap) |
| `packages/*/src/**` | `.claude/rules/gdpr-semantics.md` (widened SemVer, retention, audit, idempotency) |
| `**/tests/**` | `.claude/rules/testing.md` |
| `.github/**`, justfile, release-please files | `.claude/rules/ci.md` |
| Any `.md`, docstring wording, or `site/**` | `.claude/rules/docs.md` |
| Git anything | `.claude/rules/git-workflow.md` |

## Build & test

```bash
just check        # ruff lint + format check + mypy --strict + file-length gate
just test         # pytest, unit + property (integration excluded)
just test-pg      # integration tests (needs EFFACED_TEST_DATABASE_URL)
just fmt          # ruff format + autofix
uv sync --all-packages
just site-dev     # docs/marketing site dev server (regenerates API reference first)
just site-build   # production site build into site/dist/
```

## Non-negotiables

1. **Widened SemVer:** any change to *what gets deleted or exported* is MAJOR — declare it in the PR, label `breaking`.
2. **Strict typing:** mypy --strict + pydantic plugin stay at zero errors. Everything explicitly typed.
3. **Architecture:** one concept per file, file named after its class, 600-line cap (CI-gated). Packages over modules.
4. **Audit log is append-only by construction** — no update/delete surface, no PII in events.
5. **Resolver/AuditSink protocols are public API** — additive evolution only; resolver erasure is idempotent ("already gone" = success).
6. **Manifest format changes** bump `MANIFEST_SCHEMA_VERSION` + ship a forward migration; old manifests are never rejected.
7. **Wording:** never claim effaced makes anyone compliant.
8. **Commits:** Conventional + DCO `-s`; no Claude attribution; PRs target `stage`, never `main`.

## Workflows

| Skill | Use |
|---|---|
| `/commit` | simplify → distill learnings → checks → tests → signed commit |
| `/pr-review` | spawn reviewer agent; inline comments on the PR |
| `/release` | open promotion PR stage→main (release-please does the rest) |
| `/adr "title"` | scaffold docs/decisions/NNNN |
| `/revise-rules` | freshness-audit all rules & CLAUDE.md files |

Agents: `reviewer` (Opus, PR reviews), `python-expert` (implementation), `test-writer` (pytest + hypothesis).

## Branching

`feat/<slug>` → PR → `stage` (default) → `/release` promotion PR → `main` → release-please tags `effaced-vX.Y.Z` → PyPI Trusted Publishing → main auto-synced back to stage. Never push tags or edit versions/CHANGELOGs by hand.

## Self-iterating docs loop

Append non-obvious discoveries to `## Learnings` below as you work. `/commit` distills them into `.claude/rules/*.md` and clears the section. **Stale guidance is a bug**: when code makes a rule/CLAUDE.md/README claim untrue, fix the doc in the same PR. PRs changing public API or commands must update the matching rule (reviewer checks).

## Gotchas

- The PostToolUse hook auto-formats and auto-fixes edited `.py` files — it removes imports that are momentarily unused, so add imports and their usages in the same edit.
- The git-guard hook blocks destructive git (`reset --hard`, `clean -f`, `checkout -- .`, bare `restore`); escape hatch `# yes-destroy` only when the user explicitly asked.
- The commit hook rejects unsigned (`-s` missing) or non-conventional commits.
- Integration tests need Postgres; they're excluded by default (`just test-pg` runs them; CI's Postgres job enforces them).

## Learnings

<!-- Append discoveries here; /commit migrates them into rules. -->

- Adding ANY plain (unannotated) column to a shared-conftest table ripples into exact-shape tests: the completeness-linter complement (`test_completeness_linter.py` flags it), the table's `fully_pii_owned` classification (`test_resolution.py` — and it can silently flip a table from row-delete to anonymize if all other columns were PK/FK/annotated), and full-row dict assertions (`test_erase_subject.py`, `test_erasure_executor.py`, `test_end_to_end_fault_injection.py`). Audit those four before extending the schema.
- `Select.with_only_columns()` recalculates the FROM list from the new columns plus later `.where()` criteria — selecting a hop-chain alias's subject-id column off `table.select()` yields the implicit join the retention sweeper needs, no `select()` import in core.
- `test_bind_tables.py::test_no_server_defaults_*` pins "python-side defaults only" with exactly one carve-out: `effaced_outbox.operation` carries a server default so the additive ALTER backfills populated outboxes (ADR 0013). New columns needing a server default must extend that test's exception list consciously.
- Capability growth on the `Resolver` protocol: a separate `@runtime_checkable class XResolver(Resolver, Protocol)` in `resolvers/` + `isinstance` narrowing at call sites keeps the base protocol literally untouched (the strictest reading of additive-only). `RectifyingResolver` is the precedent.
- The PostToolUse import-stripper also fires on *edits to a different region* of the file: adding imports in one Edit and their usages in a later Edit loses the imports — when extending an existing test/module, add usages first (or in the same Edit), then fix imports last.
- The `typos` pre-commit hook auto-"fixes" prose it misreads — a SQL keyword pluralized with a bare lowercase s (SELECT/UPDATE + "s") gets a letter doubled, and a word spliced across an f-string brace gets "completed" into a typo — and the retry then commits the corruption silently. Write `SELECT statements` / `UPDATE statements`, never split a word across an f-string brace, and re-diff any file the hook reports as "modified" before re-committing.
