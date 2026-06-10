---
paths: [".github/**", "justfile", ".pre-commit-config.yaml", "release-please-config.json", ".release-please-manifest.json"]
---

# CI/CD & release plumbing

- **Pin actions by SHA** with a `# vX` comment. Dependabot maintains the pins; never use floating tags.
- Workflows declare least-privilege `permissions` at top level and per job; jobs have `timeout-minutes`.
- Required checks on PRs: CI jobs (lint, typecheck, test, test-postgres, audit), DCO, PR title. Changing a job's `name:` breaks branch-protection required checks — update the ruleset in the same PR.
- release-please runs on `main` only, with a token minted from the `lanfermann-release-bot` GitHub App (`vars.RP_APP_ID` + `secrets.RP_APP_KEY`) so required checks run on its release PRs. Don't switch it to `GITHUB_TOKEN`.
- `publish.yml` maps tag prefix → workspace package (`effaced-v*` → `packages/effaced`, `effaced-stripe-v*` → `packages/effaced-stripe`) and publishes via Trusted Publishing (OIDC, environment `pypi`). New workspace package ⇒ extend: release-please config + manifest, publish.yml tag mapping, PyPI trusted publisher registration.
- `sync-stage.yml` merges main back into stage after pushes to main; keep its bot-actor guard.
- Merge modes: feature PRs → stage are SQUASH-merged (title becomes the commit). Promotion PRs stage → main use a MERGE COMMIT (`gh pr merge --merge`) so release-please sees every feat/fix commit; squashing a promotion PR silently produces no release.
- Labels are declarative in `.github/labels.yml` (synced by workflow) — edit the file, not the UI.
- uv is the package manager; CI uses `uv sync --locked` (lockfile is authoritative — commit `uv.lock` changes).
