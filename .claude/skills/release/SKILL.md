---
name: release
description: Open the promotion PR stage→main. release-please takes over from there (versioning, tags, changelog, PyPI). Usage - /release
---

# /release — promote stage to main

Versions are computed by release-please from Conventional Commits — this skill never edits version numbers or pushes tags.

## Pre-flight

1. On `stage`, clean tree, synced with origin (`git fetch && git status`).
2. CI green on stage HEAD: `gh run list --branch stage --limit 5` (or `gh pr checks` on the last merged PR).
3. No open PRs labeled `breaking` that should ride along — ask the user if any are close.

## Promote

4. Open the promotion PR:
   ```bash
   gh pr create --base main --head stage \
     --title "chore: promote stage to main" \
     --body "$(git log origin/main..origin/stage --pretty='- %s' | head -50)"
   ```
5. **Merge mode matters:** the promotion PR is merged with a MERGE COMMIT (`gh pr merge --merge`), never squashed — squashing would collapse stage's feat/fix commits into one chore commit and release-please would compute no release. (Feature PRs to stage stay squash-only.)
6. After merge: release-please opens per-package release PRs on main (`chore(main): release effaced X.Y.Z`) — those are squash-merged. Merging tags `effaced-vX.Y.Z` / `effaced-stripe-vX.Y.Z`, publishes GitHub Releases, triggers PyPI Trusted Publishing, and `sync-stage.yml` merges main back into stage.

## Don'ts

- Never commit directly to `main`; never `git tag`; never edit `pyproject.toml` versions or CHANGELOGs by hand — release-please owns all of it.
