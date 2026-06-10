# Runbook: one-time external setup (manual, owner-only)

Status of the three things automation cannot self-provision:

## 1. PyPI Trusted Publishing (required before first release)

For **each** of `effaced` and `effaced-stripe` on pypi.org → project (or pending publisher for unclaimed names) → Publishing → add trusted publisher:

- Owner: `jaylann`, Repository: `effaced`
- Workflow: `publish.yml`
- Environment: `pypi`

Also create the GitHub environment: repo → Settings → Environments → `pypi` (optionally add a protection rule requiring your approval before publish jobs run).

## 2. lanfermann-release-bot (required for release PRs to get CI)

The same GitHub App used by XCStringsTranslator (App ID 3987809):

1. github.com/settings/apps → lanfermann-release-bot → Install App → add `jaylann/effaced`.
2. `gh secret set RP_APP_KEY -R jaylann/effaced < path/to/private-key.pem`
3. `RP_APP_ID` repo variable is already set to `3987809`.

Until installed, `release-please.yml` fails at token minting — harmless before the first promotion to main.

## 3. Kodus (self-hosted reviewer)

Connect `jaylann/effaced` in the Kodus dashboard. Config is in-repo (`kodus-config.yml`, rules in `.kody/rules/`); base branches are `stage` and `main`.
