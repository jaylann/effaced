# Runbook: adding a new workspace package (and shipping it to PyPI)

Checklist for adding a package under `packages/<name>` and getting it onto
PyPI. Most of this is mechanical; the **PyPI Trusted Publisher** section is
where the first 0.1.0 rollout actually bled, so read it before you start.

## 1. The package itself

- `packages/<name>/` with its own `pyproject.toml` (`version = "0.0.0"` — never
  hand-edit versions after this; release-please owns them), `src/<name>/`, and
  `tests/`. Copy the shape of `packages/effaced-stripe`.
- Classifiers include `Development Status :: 2 - Pre-Alpha` until it's proven.
- `uv sync --all-packages` then commit the `uv.lock` change.

## 2. Release plumbing (all four, or the release silently misbehaves)

1. **`release-please-config.json`** → add a `packages/<name>` entry (copy an
   existing one: `release-type: python`, `package-name`, `component`,
   `changelog-path`).
2. **`.release-please-manifest.json`** → add `"packages/<name>": "0.0.0"`.
3. **`publish.yml`** → add the tag→package mapping in the *Resolve package from
   tag* step (`<name>-v*) echo "package=<name>"`).
4. **PyPI Trusted Publisher** → see §3. This is manual and outside automation.

The `target-branch: main`, `uv.lock` re-lock, and DCO bot-exemption in
`release-please.yml`/`dco.yml` are already generic — a new package inherits them
for free. (They were the three first-run bugs fixed for 0.1.0; don't undo them.)

## 3. PyPI Trusted Publisher — the pending-publisher tuple trap

A **pending** publisher (used to claim a not-yet-existing project name via OIDC)
must be **unique on `(owner, repo, workflow, environment)`**
([warehouse #16920](https://github.com/pypi/warehouse/issues/16920)). Every
effaced package currently shares the *same* tuple — `(jaylann, effaced,
publish.yml, pypi)` — and the project name is **not** part of the key. So:

- You can have **at most one pending publisher** for that tuple at a time.
  Trying to add a second 503s. (Symptom that wasted hours on 0.1.0: "Add" on the
  publishing form fails only for the second package; changing the repo field
  "works" — that's the tuple changing.)
- Once a package **publishes its first release**, its pending publisher is
  consumed into a normal (project-attached) publisher and **no longer holds the
  tuple as pending** — active publishers may share the tuple freely.

### Adding ONE package to an already-published set (the normal case)

No pending publisher currently holds the tuple (existing packages are all
active), so just:

1. pypi.org → Publishing → add pending publisher: name `<name>`, owner
   `jaylann`, repo `effaced`, workflow `publish.yml`, environment `pypi`.
2. Release normally (combined release PR is fine).

### Adding TWO+ brand-new packages at once (what 0.1.0 hit)

They'd need two pending publishers with the same tuple simultaneously — blocked.
Two ways out:

- **Sequence them (what we did):** set `separate-pull-requests: true` in
  `release-please-config.json`, ship package A first (consuming its pending
  publisher), then add package B's pending publisher (tuple now free) and ship
  it. Flip `separate-pull-requests` back off afterward. Slow but no code changes.
- **Distinct environment per package (cleaner long-term):** give each package
  its own GitHub Environment (`pypi`, `pypi-<name>`, …) so the tuples differ and
  pending publishers never collide. Requires `publish.yml` to resolve the
  `environment:` per package (from the tag) and a matching environment on each
  PyPI pending publisher. Consider migrating to this if multi-package releases
  become common.

## 4. First release

Follow `release.md`: promote `stage → main` with a **merge commit**,
squash-merge the release PR(s) release-please opens. Merging tags
`<name>-vX.Y.Z`, publishes the GitHub Release, and `publish.yml` pushes to PyPI
via Trusted Publishing. Verify: `curl -s https://pypi.org/pypi/<name>/json`.

> The release PR needs **1 approving review** (main ruleset) even though it's a
> bot PR — the author is the bot, so the owner can approve it.

## 5. Other touchpoints

- README package table + install lines; `site/` if the package is user-facing.
- Labels: add `area:<name>` to `.github/labels.yml` if it's a new area.
- `CLAUDE.md` package table / scope line.
