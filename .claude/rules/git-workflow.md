---
paths: ["**"]
---

# Git & release workflow

## Branches
- `stage` is the default branch — all PRs target it. `main` is release-only; never commit or PR feature work to `main` directly.
- Feature branches: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`.
- Flow: `feat/x → stage → (promotion PR via /release) → main → release-please → PyPI → main auto-synced back to stage`.

## Commits
- **Conventional Commits**, lowercase subject, ≤72 chars: `type(scope)?: subject`. Types: `feat fix chore docs refactor test perf style ci build revert`.
- **DCO sign-off on every commit**: `git commit -s`. The PreToolUse hook blocks unsigned commits; CI rejects them too.
- **No Claude attribution**: `includeCoAuthoredBy` is false in settings — never add `Co-Authored-By: Claude` trailers manually either.
- Squash-merge only; the PR title becomes the commit on stage, so PR titles follow the same convention (CI-enforced).
- The `typos` pre-commit hook auto-"fixes" prose it misreads — a SQL keyword pluralized with a bare lowercase s (SELECT/UPDATE + "s") gets a letter doubled, and a word spliced across an f-string brace gets "corrected" into a typo — and the retry then commits the corruption silently. Write `SELECT statements` / `UPDATE statements`, never split a word across an f-string brace, and re-diff any file the hook reports as "modified" before re-committing.

## Don't destroy work
- `git reset --hard`, `git clean -f`, `git checkout -- .`, bare `git restore <path>` are blocked by the git-guard hook — that block is correct, not an obstacle. Inspect with `git show <ref>:<file>` / `git diff <ref>`; park work with `git stash`. Only append `# yes-destroy` when the user asked for the discard in their current message.

## PRs & issues
- **Always label.** Every PR *and* every issue you open: at least one `type:*` label + relevant `area:*` labels. PRs also answer the PR-template "Erasure/export semantics" section and need `just check` + `just test` green locally first.
- **Always wire dependencies.** When an issue or PR is blocked by another (or a body says "Blocked by #N"), record it with GitHub's native issue-dependencies API — don't leave the relationship as prose only. The inverse "blocks" edge is created automatically, so only set `blocked_by`:
  ```bash
  # issue_id is the internal integer id, NOT the issue number — fetch it first:
  gh api repos/:owner/:repo/issues/<blocker> --jq '.id'
  # add the edge with -F (integer); -f sends a string and 422s:
  gh api --method POST repos/:owner/:repo/issues/<blocked>/dependencies/blocked_by -F issue_id=<blocker-id>
  # inspect: gh api repos/:owner/:repo/issues/<n>/dependencies/blocked_by --jq '[.[].number]'
  ```
- **Addressed review comments get resolved, not just answered.** After replying to a review thread you've addressed (fix pushed or rationale given), mark the thread resolved too — an answered-but-open thread reads as pending work:
  ```bash
  # thread ids: gh api graphql -f query='query { repository(owner:"jaylann", name:"effaced") { pullRequest(number:<N>) { reviewThreads(first:50) { nodes { id isResolved path } } } } }'
  gh api graphql -f query='mutation { resolveReviewThread(input:{threadId:"<id>"}) { thread { isResolved } } }'
  ```
- Working a GitHub issue: if the fix is verified, commit (your changes only) and close the issue with a fitting comment. If you can't fully fix it, comment your findings. Unrelated problems you notice → file a new issue, don't fix inline.
- If two consecutive state queries (`gh pr checks`, `gh issue view`, …) return identical output, stop polling.

## Releases (automated — don't hand-roll)
- Never push tags manually; release-please owns tags (`effaced-vX.Y.Z`, `effaced-stripe-vX.Y.Z`), changelogs, and GitHub Releases.
- `fix:` → patch, `feat:` → minor, `feat!:`/`BREAKING CHANGE:` → major — and remember the widened rule: erasure/export behaviour changes are major regardless of syntax.
- Publishing is PyPI Trusted Publishing (OIDC) from `publish.yml` — there are no PyPI tokens to manage, don't add any.
