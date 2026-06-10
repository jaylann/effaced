# 0002. stage→main branching with release-please automation

- **Status:** accepted
- **Date:** 2026-06-10

## Context

Maintenance should mostly run itself: versioning, changelogs, tags, and PyPI publishing must not depend on manual ritual. At the same time daily work should land continuously without each merge implying a release.

## Decision

Two-branch model: `stage` (default; all PRs target it; always green) and `main` (release-only). A promotion PR (`/release` skill) moves stage to main; release-please (manifest mode, multi-component) then opens per-package release PRs on main. Merging one tags, publishes a GitHub Release, and triggers PyPI **Trusted Publishing** (OIDC — no tokens). `sync-stage.yml` merges main back into stage. Release PRs are opened by the `lanfermann-release-bot` GitHub App so required CI checks run on them.

## Consequences

- Versions/changelogs derive from Conventional Commits — enforced at commit (hook), PR title (CI), and squash-merge.
- Nobody ever pushes a tag or edits a version by hand; doing so breaks the automation contract.
- Cutting a release is: merge promotion PR, merge release PR. Two clicks.
