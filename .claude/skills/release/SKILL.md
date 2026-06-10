---
name: release
description: Open the promotion PR stage→main and merge it with a MERGE COMMIT. release-please takes over from there (versioning, tags, changelog, PyPI). Usage - /release
---

# /release — promote stage to main

Versions are computed by release-please from Conventional Commits — this skill never edits version numbers or pushes tags.

**Merge modes (important):** feature PRs → `stage` are squash-merged (one clean commit per PR). The promotion PR `stage → main` is merged with a **MERGE COMMIT** (`gh pr merge --merge`) so every squashed feat/fix commit lands on main individually — that is what release-please parses. Squashing the promotion PR collapses them into one `chore:` commit and produces NO release.

## Pre-flight

1. On `stage`, clean tree, synced with origin (`git fetch && git status`).
2. CI green on stage HEAD: `gh run list --branch stage --limit 5`.
3. No open PRs labeled `breaking` that should ride along — ask the user if any are close.

## Promote

4. Open the promotion PR:
   ```bash
   gh pr create --base main --head stage \
     --title "chore: promote stage to main" \
     --body "$(git log origin/main..origin/stage --pretty='- %s')"
   ```
5. Merge it with a merge commit (never squash):
   ```bash
   gh pr merge <num> --merge
   ```
6. After merge: release-please opens per-package release PRs on main (`chore(main): release effaced X.Y.Z`) — squash-merge those. Merging tags `effaced-vX.Y.Z` / `effaced-stripe-vX.Y.Z`, publishes GitHub Releases, triggers PyPI Trusted Publishing, and `sync-stage.yml` merges main back into stage (as the release-bot app).

## Don'ts

- Never commit directly to `main`; never `git tag`; never edit versions or CHANGELOGs by hand.
- Never squash the promotion PR.
