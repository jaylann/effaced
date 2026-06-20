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

- mutmut's per-mutant verdicts were non-deterministic until #124: the copied-tree run never loads the workspace-root conftest, so property tests ran on hypothesis defaults (200ms deadline → spurious kills under load; random seeds → kill status oscillating between full runs). Fixed by the `mutation` profile (`deadline=None, derandomize=True`) that `packages/effaced/tests/conftest.py` self-activates under a `mutants/` path. Distilled into testing.md.
- A targeted `mutmut run <name>` can disagree with the full run's verdict for hypothesis-covered mutants AND resets every other mutant to "not checked" — only full-run results feed the gate.
- `model_dump(mode=...)`: an unrecognized mode string falls back to python-mode; for models whose fields are all str/StrEnum the JSON serialization is identical either way (made `mode="JSON"` mutants equivalent rather than killable).
- Composite subject keys (ADR 0025) store ONE canonical escaped string in the unchanged `subject_id`/`subject_ref` columns, so the audit trail and outbox hold the canonical form, not the tuple. `scoping.subject_values` closes the round-trip: a bare `str` against a multi-column graph is read as a *canonical composite string* and `parse_canonical`'d back, so replay/requeue (which re-feed `entry.subject_id` — a canonical str from the trail — to `erase_subject`) re-decompose the same key. A single-column graph always treats a bare str as one value (byte-identical). The arity guard fires only after this decode.
- `scoping.py` lives in `adapters/sqlalchemy/` but the Exporter and RetentionSweeper are core; they import `subject_scope` *up* from the adapter so there is one composite predicate. The semgrep gate only matches a direct `import sqlalchemy`/`from sqlalchemy`, so importing an adapter module from core passes — and `import effaced` already hard-depends on the SQLAlchemy adapter through the root re-exports. A forked second predicate on the identity-matching path is the worse risk; core cannot host the predicate because a composite row-value equality needs `tuple_`, which core may not import.
- Storage/domain models accept `SubjectIdentifier` but store a `str` via `Annotated[SubjectIdentifier, BeforeValidator(normalize_subject_id), Field(max_length=255)]`: the before-validator collapses a `CompositeSubjectId` to its canonical string, so mypy accepts a composite at the call site while the column holds the scalar — no owned-table DDL change. effaced-fastapi `Subject.subject_id` deliberately does NOT normalize: it carries the composite through to the engine for SQL decomposition.
- Audit hash chain (ADR 0028): a content hash MUST bind the timestamp's *instant*, not its `isoformat()` text. A `timestamptz` round-trips back **aware** on psycopg but **naive** on SQLite (tzinfo dropped), so hashing `occurred_at.isoformat()` makes the append-time and verify-time digests differ by a `+00:00` suffix → every row reads as tampered. Fixed by `hash_chain._canonical_occurred_at`: normalize to UTC (aware→`astimezone`, naive→assumed-UTC), then emit a fixed offset-free `strftime` string — aware-UTC, naive-UTC, and same-instant-different-offset all collapse to one encoding. Any cross-driver field encoded into a content hash needs the same instant/value canonicalization, never the raw str form. The chain is computed at the SINK boundary (`DatabaseAuditSink.append` in its own per-append txn, writing both hash cols); `AuditEvent` and the `AuditSink` protocol stay untouched — verification is `AuditChainVerifier`, a standalone object reading via the table handle (the `ReplaySource`-not-a-sink-method precedent, ADR 0023). NULL-hash rows are unchained: skipped, not failed.
- Audit chain MUST be keyed by *insertion order*, NOT `occurred_at` (reviewer-caught HIGH on #157): `occurred_at` is caller-backdatable — `consent/ledger.py` and `restriction/ledger.py` set `occurred_at=record.recorded_at`. The first design chained the sink to the global-max `(occurred_at, event_id)` row while the verifier re-derived by sorting ascending on that key; they agree only when `occurred_at` is monotonic with insertion, which the public API breaks (append A@t2 then backdated B@t1 → false break on a clean trail — the exact false alarm the unchained-prefix rule exists to avoid). Fix: a pure linked list — sink chains to the current TAIL (the one chained row whose `event_hash` no row cites as `prior_hash`); verifier walks from genesis (`prior_hash IS NULL`) along `prior_hash`→`event_hash` pointers, detecting content mismatch, FORK (two children of one predecessor — what concurrent appends produce) and ORPHAN/missing-genesis (deterministic break = min `event_id`). No new DDL. Lesson: never order a tamper-evidence chain by a mutable/caller-supplied field; key it by the immutable structure (the pointer), and prove it with a NON-MONOTONIC-timestamp property test, not strictly-increasing ones (those engineer the bug away).
