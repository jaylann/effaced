# effaced

GDPR data-subject mechanisms — Art. 15 export, Art. 17 erasure, Art. 7 consent, append-only audit — across the user's own database and external systems (resolvers; Stripe, Supabase, S3, and Resend first). uv workspace monorepo: `packages/effaced` (core) + `packages/effaced-stripe` + `packages/effaced-supabase` + `packages/effaced-s3` + `packages/effaced-resend` + `packages/effaced-fastapi` (web layer, ADR 0020), plus `site/` (Astro Starlight docs + marketing, pnpm, outside the uv workspace — ADR 0011). **We ship mechanisms, never compliance determinations.**

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
- ADR 0026: `EffacedStack.from_base`/`from_manifest` wire a `SubjectErasureLock` into the planner BY DEFAULT (the stack is the supported one-call wiring; the guard is the correct default there). Consequence for tests: any stack test that *erases* now needs `effaced_subject_erasures` to exist, so it must run `Base.metadata.create_all(engine)` AFTER `from_base`/`from_manifest` mounts the owned tables (the same idempotent-create pattern `test_from_base_enqueues_external_refs` already used). A test that relied on a prior test having mounted+created the table passes in a full run but fails in isolation/random order. A hand-built `ErasurePlanner` still defaults to `lock=None` (byte-identical), so opting OUT is constructing the planner directly without a lock.
- The `SubjectErasureLock` tombstone upsert must NOT use `session.begin_nested()` as the first statement on a fresh session: the SAVEPOINT auto-begins the outer transaction and, on release, can auto-commit it, leaking the tombstone past the caller's `rollback`. Postgres uses `INSERT ... ON CONFLICT DO UPDATE` (also row-locks for serialization); other dialects do a plain read-then-`UPDATE`/`INSERT` (correct because only Postgres needs concurrency safety, and it respects the caller's rollback so `mark_erased`'s same-transaction durability holds).

- mutmut's per-mutant verdicts were non-deterministic until #124: the copied-tree run never loads the workspace-root conftest, so property tests ran on hypothesis defaults (200ms deadline → spurious kills under load; random seeds → kill status oscillating between full runs). Fixed by the `mutation` profile (`deadline=None, derandomize=True`) that `packages/effaced/tests/conftest.py` self-activates under a `mutants/` path. Distilled into testing.md.
- A targeted `mutmut run <name>` can disagree with the full run's verdict for hypothesis-covered mutants AND resets every other mutant to "not checked" — only full-run results feed the gate.
- `model_dump(mode=...)`: an unrecognized mode string falls back to python-mode; for models whose fields are all str/StrEnum the JSON serialization is identical either way (made `mode="JSON"` mutants equivalent rather than killable).
- Composite subject keys (ADR 0025) store ONE canonical escaped string in the unchanged `subject_id`/`subject_ref` columns, so the audit trail and outbox hold the canonical form, not the tuple. `scoping.subject_values` closes the round-trip: a bare `str` against a multi-column graph is read as a *canonical composite string* and `parse_canonical`'d back, so replay/requeue (which re-feed `entry.subject_id` — a canonical str from the trail — to `erase_subject`) re-decompose the same key. A single-column graph always treats a bare str as one value (byte-identical). The arity guard fires only after this decode.
- `scoping.py` lives in `adapters/sqlalchemy/` but the Exporter and RetentionSweeper are core; they import `subject_scope` *up* from the adapter so there is one composite predicate. The semgrep gate only matches a direct `import sqlalchemy`/`from sqlalchemy`, so importing an adapter module from core passes — and `import effaced` already hard-depends on the SQLAlchemy adapter through the root re-exports. A forked second predicate on the identity-matching path is the worse risk; core cannot host the predicate because a composite row-value equality needs `tuple_`, which core may not import.
- Storage/domain models accept `SubjectIdentifier` but store a `str` via `Annotated[SubjectIdentifier, BeforeValidator(normalize_subject_id), Field(max_length=255)]`: the before-validator collapses a `CompositeSubjectId` to its canonical string, so mypy accepts a composite at the call site while the column holds the scalar — no owned-table DDL change. effaced-fastapi `Subject.subject_id` deliberately does NOT normalize: it carries the composite through to the engine for SQL decomposition.
