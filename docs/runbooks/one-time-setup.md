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
4. Add the app as bypass actor on BOTH rulesets so it can push the main→stage sync (and so release-please can manage its branches). For each ruleset id (`gh api repos/jaylann/effaced/rulesets --jq '.[].id'`):
   ```bash
   gh api repos/jaylann/effaced/rulesets/<id> --jq '{name,bypass_actors}'   # inspect
   # then PUT the ruleset back with bypass_actors:
   #   [{"actor_id": 3987809, "actor_type": "Integration", "bypass_mode": "always"}]
   ```
   (Repo → Settings → Rules → ruleset → Bypass list → add the app works too.)

Until installed, `release-please.yml` and `sync-stage.yml` fail at token minting — harmless before the first promotion to main.

## 3. Kodus (self-hosted reviewer)

Connect `jaylann/effaced` in the Kodus dashboard. Config is in-repo (`kodus-config.yml`, rules in `.kody/rules/`); base branches are `stage` and `main`.
