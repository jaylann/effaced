---
paths: [".github/**", "justfile", ".pre-commit-config.yaml", "release-please-config.json", ".release-please-manifest.json"]
---

# CI/CD & release plumbing

- **Pin actions by SHA** with a `# vX` comment. Dependabot maintains the pins; never use floating tags.
- Workflows declare least-privilege `permissions` at top level and per job; jobs have `timeout-minutes`.
- Required checks on PRs: CI jobs (lint, typecheck, test, test-lowest, test-postgres, audit, semgrep, workflow-lint), DCO, PR title. Changing a job's `name:` breaks branch-protection required checks — update the ruleset in the same PR.
- **`.semgrep/` is where GDPR invariants live as CI** (append-only audit, no SQLAlchemy outside adapters, no `model_construct`, one asyncio.run bridge, no PII keys in audit payloads). New structural invariant ⇒ new rule there, and prove it fires against a deliberately-violating scratch file before trusting it. The semgrep/zizmor/twine/cyclonedx pins in workflows and justfile are version-pinned by hand — bump them deliberately, keep justfile and ci.yml pins identical.
- Coverage is gated: `fail_under` in `[tool.coverage.report]` applies to every `--cov` run (CI test matrix and `just cov`). Don't lower it to make a PR pass — write the missing test.
- Hypothesis profiles (`ci`, `deep`) are registered in the root `conftest.py`; the CI test job passes `--hypothesis-profile=ci` and caches `.hypothesis/examples` so found failures replay across runs. `deep-checks.yml` runs the `deep` profile + mutmut weekly (mutation score is report-only until a baseline exists — see `[tool.mutmut]` in pyproject).
- `test-lowest` re-locks with `--resolution lowest-direct` to prove dependency floors are real. If it fails after a dep bump, raise the floor in the package's pyproject (`fix(deps):`) — never restore it to green by pinning CI.
- Publish supply chain: `twine check` + CycloneDX SBOM + `actions/attest-build-provenance` in the build job, `attestations: true` (PEP 740) on the PyPI publish step, SBOM attached to the GitHub release. Keep these when touching publish.yml.
- release-please runs on `main` only, with a token minted from the `lanfermann-release-bot` GitHub App (`vars.RP_APP_ID` + `secrets.RP_APP_KEY`) so required checks run on its release PRs. Don't switch it to `GITHUB_TOKEN`.
- `publish.yml` maps tag prefix → workspace package (`effaced-v*` → `packages/effaced`, `effaced-stripe-v*` → `packages/effaced-stripe`) and publishes via Trusted Publishing (OIDC, environment `pypi`). New workspace package ⇒ extend: release-please config + manifest, publish.yml tag mapping, PyPI trusted publisher registration.
- `sync-stage.yml` merges main back into stage after pushes to main; keep its bot-actor guard.
- Merge modes: feature PRs → stage are SQUASH-only (stage ruleset enforces; one clean commit per PR). Promotion PRs stage → main use a MERGE COMMIT (`gh pr merge --merge`; main's ruleset allows merge+squash) so release-please sees every feat/fix commit individually — squashing a promotion PR silently produces no release. release-please's own release PRs on main are squashed.
- `sync-stage.yml` authenticates as the lanfermann-release-bot app (ruleset bypass actor) to push the main→stage sync; the GITHUB_TOKEN cannot push to stage.
- Labels are declarative in `.github/labels.yml` (synced by workflow) — edit the file, not the UI.
- uv is the package manager; CI uses `uv sync --locked` (lockfile is authoritative — commit `uv.lock` changes).
- **`docs.yml` builds/deploys the site** (ADR 0011): path-filtered build on PRs, build + GitHub Pages deploy on pushes to stage. Deliberately **not a required check** — path-filtered checks can't be required (a skipped check blocks merging forever). pnpm is pinned once, via `packageManager` in `site/package.json` (`pnpm/action-setup` reads it); Node version lives in the workflow. The site build needs only the `docs` dependency group (`uv sync --locked --only-group docs`) because `scripts/gen_api_docs.py` is pure static analysis — keep it import-free of the workspace packages or that CI job breaks. effaced.dev later = set `SITE_URL`/`BASE_PATH` env on the build step + swap the deploy job.
