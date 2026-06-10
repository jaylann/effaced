# Runbook: cutting a release

Everything is automated; a release is two PR merges.

1. **Promote**: run `/release` (or manually `gh pr create --base main --head stage --title "chore: promote stage to main"`). Pre-conditions: stage green, no half-landed breaking work.
2. **Merge the promotion PR with a merge commit** (`gh pr merge --merge`) — never squash it, or release-please loses the individual feat/fix commits and computes no release. release-please now opens/updates release PRs on main — one per package with changes (`chore(main): release effaced X.Y.Z`).
3. **Merge the release PR(s).** This:
   - tags `effaced-vX.Y.Z` / `effaced-stripe-vX.Y.Z`
   - publishes the GitHub Release with the generated changelog
   - triggers `publish.yml` → builds with uv → publishes to PyPI via Trusted Publishing
   - `sync-stage.yml` merges main back into stage
4. **Verify**: `gh run list --branch main --limit 3` green; package visible on PyPI.

## Recovery

- Publish failed after tag: fix, then re-run `publish.yml` via workflow_dispatch with the tag (publishing is `skip-existing: true`, safe to retry).
- Never delete tags or releases; ship a follow-up patch instead.
- Yanking on PyPI is reserved for data-protection-critical erasure bugs — and gets a Security changelog entry.
