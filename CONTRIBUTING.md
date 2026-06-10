# Contributing to effaced

Thanks for helping build trustworthy GDPR machinery. This guide covers setup, conventions, and how a change becomes a release.

## Setup

```bash
git clone https://github.com/jaylann/effaced && cd effaced
uv sync                                  # workspace: packages/effaced + packages/effaced-stripe
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
just check                               # ruff lint + format check + mypy + file-length gate
just test                                # pytest (unit + property tests)
```

Requires [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just) (`brew install just`).

## Branch flow

```
feat/your-change ──PR──► stage (default) ──promotion PR──► main ──► release-please ──► PyPI
```

- PRs always target **`stage`**, never `main`. `main` is release-only.
- Squash-merge: your PR title becomes the commit on `stage`, so PR titles must be Conventional Commits too (CI enforces this).

## Commits — Conventional + DCO

Every commit message:

1. **Conventional Commits**: `type(scope)?: lowercase subject` — types: `feat fix chore docs refactor test perf style ci build revert`. `fix:` → patch, `feat:` → minor, `feat!:`/`BREAKING CHANGE:` → major.
2. **DCO signed-off**: commit with `git commit -s`, which adds `Signed-off-by: Your Name <you@example.com>`. By signing off you certify the [Developer Certificate of Origin](https://developercertificate.org/) — that you have the right to contribute the code under this project's license. CI rejects unsigned commits.

## Code standards

- **Strict typing everywhere.** mypy `--strict` with the pydantic plugin must pass. No untyped seams; no `Any` where a real type exists.
- **Pydantic models for data, validators for invariants.** Domain objects are frozen `BaseModel`s with `extra="forbid"`; cross-field rules live in `model_validator`s, not call sites.
- **Small, searchable files.** One concept per file, named after the class it holds (`pii_spec.py` → `PiiSpec`), hard cap 600 lines (CI-gated). Packages over modules: split, don't cram.
- **Google-style docstrings** on all public modules, classes, and functions (ruff `D` rules enforce this). The docstrings are the future API reference — write them like documentation, because they will be.
- **Tests for behaviour**, property-based tests (hypothesis) for format/isolation guarantees. Anything touching erasure or export semantics needs tests proving no cross-subject bleed and retained-category preservation.

## The widened SemVer rule (read this one)

For effaced, **"breaking" includes behaviour**: a change to *what gets deleted or exported* is a MAJOR change even if no signature changed — silently changing compliance behaviour is the worst failure a library like this can have. Likewise:

- Manifest-format changes: MAJOR, with a forward migration in `effaced/manifest/migration.py` (old manifests are migrated, never rejected).
- `Resolver` and `AuditSink` protocols are public API: extend additively only (optional methods with defaults); never break custom implementations.
- Deprecations get a runway: warning → window → removal in the next major. Nothing is yanked.

If your PR changes erasure/export behaviour, say so explicitly in the PR body — the template asks.

## How releases work (you don't do anything)

1. Your PR merges to `stage`.
2. A maintainer opens a promotion PR `stage → main`.
3. On merge, [release-please](https://github.com/googleapis/release-please) opens/updates per-package release PRs (version bump + changelog from your commit types).
4. Merging a release PR tags (`effaced-vX.Y.Z` / `effaced-stripe-vX.Y.Z`), creates the GitHub Release, and publishes to PyPI via Trusted Publishing (OIDC — no tokens anywhere).
5. `main` is synced back into `stage` automatically.

## Issues & PRs

- Bug reports: use the issue form; include package versions and a minimal annotated model if manifest-related.
- New resolvers: open a "Resolver request" issue first — first-party resolvers are added demand-pulled.
- Every PR needs at least one `type:*` label and the relevant `area:*` labels.

## Not legal advice

Contributions must keep the wording discipline: effaced ships *mechanisms*, never compliance determinations. Don't add copy (docs, docstrings, log messages) claiming effaced "makes you compliant".
