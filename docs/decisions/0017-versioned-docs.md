# 0017. Versioned docs via starlight-versions, snapshot at 0.1

- **Status:** accepted (amends 0011)
- **Date:** 2026-06-12

## Context

ADR 0011 stood up the Astro Starlight site and explicitly **deferred versioned docs** ("starlight-versions when a 1.0-adjacent release exists; both packages are 0.x today"). That deferral has bitten sooner than 1.0: `effaced` and `effaced-stripe` shipped `0.1.0` to PyPI, but `stage` HEAD has since grown the S3 and Supabase resolvers, rectification, restriction, and the retention sweep. A reader on the released `0.1.0` who lands on the live docs sees an API surface their installed version does not have — `effaced_s3`, `Rectifier`, restriction concepts — with no way to read the docs as they stood at their release. Docs that describe `stage` are correct for contributors and wrong for users. Issue #49 tracks closing that gap (and, separately, the `effaced.dev` custom domain).

The custom-domain half of #49 is **still deferred** here — DNS is not ready, and `astro.config.mjs` already reads `SITE_URL`/`BASE_PATH` from the environment (ADR 0011), so that switch remains workflow-env-only and untouched by this ADR.

## Decision

- **Adopt the `starlight-versions` community plugin** (HiDeoo). It is compatible with the site's stack: `0.8.0` added Astro 6 support, `0.9.0` raised its Starlight floor to `>=0.39.0`, and the site is on `@astrojs/starlight ^0.40` / `astro ^6.4.5`. Pinned via `site/package.json` (pnpm).
- **`stage` HEAD is the current "Latest" docs**; the version switcher offers exactly one frozen snapshot, **`0.1`**, for the released `0.1.0` line. The plugin snapshots the current docs into `site/src/content/docs/0.1/docs/…` and writes a sidebar snapshot to `site/src/content/versions/0.1.json`; both are **committed**, not regenerated per build (the plugin only snapshots a slug whose directory is absent).
- **The `0.1` snapshot reflects the released `0.1.0` API, not `stage` HEAD.** It was produced from the `effaced-v0.1.0` tag: that checkout's `scripts/gen_api_docs.py` rendered the 14-page `0.1.0` reference (no `effaced_s3`/`effaced_supabase`/rectification/restriction/retention), and the tag's hand-written pages and sidebar were snapshotted alongside it. So the `0.1` switcher entry serves the docs a `0.1.0` user actually runs.
- **The current reference stays generated + gitignored; versioned snapshots are tracked.** `.gitignore` ignores only `site/src/content/docs/docs/reference/*` (the HEAD reference). Snapshot reference pages live under `docs/<slug>/docs/reference/` and are deliberately outside that ignore, so they are committed and frozen. `scripts/gen_api_docs.py` clears only its own `OUT_DIR` (`docs/docs/reference/*.mdx`), so `just site-gen` never touches a snapshot.
- **Snapshot cadence: one per minor/major release.** Cut a new versioned snapshot when a `0.N.0` (or `1.0.0`) tag lands, capturing that tag's generated reference and hand-written docs — see `.claude/rules/docs.md` for the procedure.

## Consequences

- `site/src/content.config.ts` gains the `versions` collection (`docsVersionsLoader`); `astro.config.mjs` gains the plugin and the `0.1` version. Both are additive site config, no Python or package change.
- The published site grows a frozen `/0.1/…` tree that must not be hand-edited to track `stage` — it is a release artifact. Corrections to `0.1.0` docs are out of scope unless a `0.1.x` patch reissues them.
- `docs.yml` needs no change: the snapshot is plain committed content that builds with the site; CI still installs only the `docs` dependency group for the generator (snapshots are pre-generated, not regenerated in CI).
- The wording discipline (mechanisms, never compliance determinations) binds snapshot content exactly as it binds current docs — the `0.1` snapshot inherits it from the tag, which already complied.
- Custom domain remains the open half of #49.
