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

## Don't destroy work
- `git reset --hard`, `git clean -f`, `git checkout -- .`, bare `git restore <path>` are blocked by the git-guard hook — that block is correct, not an obstacle. Inspect with `git show <ref>:<file>` / `git diff <ref>`; park work with `git stash`. Only append `# yes-destroy` when the user asked for the discard in their current message.

## PRs & issues
- Every PR: at least one `type:*` label + relevant `area:*` labels, the PR-template "Erasure/export semantics" section answered, `just check` + `just test` green locally first.
- Working a GitHub issue: if the fix is verified, commit (your changes only) and close the issue with a fitting comment. If you can't fully fix it, comment your findings. Unrelated problems you notice → file a new issue, don't fix inline.
- If two consecutive state queries (`gh pr checks`, `gh issue view`, …) return identical output, stop polling.

## Releases (automated — don't hand-roll)
- Never push tags manually; release-please owns tags (`effaced-vX.Y.Z`, `effaced-stripe-vX.Y.Z`), changelogs, and GitHub Releases.
- `fix:` → patch, `feat:` → minor, `feat!:`/`BREAKING CHANGE:` → major — and remember the widened rule: erasure/export behaviour changes are major regardless of syntax.
- Publishing is PyPI Trusted Publishing (OIDC) from `publish.yml` — there are no PyPI tokens to manage, don't add any.
