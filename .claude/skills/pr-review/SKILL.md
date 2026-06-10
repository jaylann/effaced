---
name: pr-review
description: Spawn the reviewer agent to post inline review comments on a PR. Usage - /pr-review [pr-number] (defaults to current branch's PR).
---

# /pr-review — agent review with inline comments

1. **Resolve the PR**: use the given number, else `gh pr view --json number -q .number` for the current branch. No PR → tell the user and stop.
2. **Skip conditions**: merged/closed PRs, `status:wip` label.
3. **Spawn the `reviewer` agent** (subagent_type: reviewer) with:
   > Review PR #N in jaylann/effaced. Read the full diff and every changed file, cross-reference `.claude/rules/*.md`, and post ONE GitHub review with inline comments via `gh api`. `COMMENT` by default; `REQUEST_CHANGES` for blockers (cross-subject bleed, retention violations, audit integrity, undeclared behaviour change to deletion/export, non-additive protocol changes). Never APPROVE. Return PR url, comment count, blocker count.
4. **Report** the reviewer's summary verbatim, plus whether merge should wait.
