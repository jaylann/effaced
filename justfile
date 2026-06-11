# effaced dev loop — `just` lists recipes

# show available recipes
default:
    @just --list

# one-time setup after clone: deps + git hooks
bootstrap:
    uv sync --all-packages
    uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
    @echo "✓ workspace synced, pre-commit hooks installed"

# format + autofix
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# the full local gate: lint + format check + types + architecture
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
    uv run python scripts/check_file_length.py
    @echo "✓ check green"

# unit + property tests (integration excluded)
test *args:
    uv run pytest -m "not integration" {{args}}

# integration tests — needs EFFACED_TEST_DATABASE_URL (Postgres)
test-pg *args:
    uv run pytest -m integration {{args}}

# everything CI runs, locally
ci: check test

# coverage report (gated: fails under the pyproject fail_under floor)
cov:
    uv run pytest -m "not integration" --cov --cov-report=term-missing

# domain-invariant scan — same rules and pin as the CI semgrep job
semgrep:
    SEMGREP_ENABLE_VERSION_CHECK=0 uvx semgrep@1.165.0 scan --config .semgrep --error --metrics=off

# lint the workflows themselves — same zizmor pin as CI
lint-actions:
    uvx zizmor@1.25.2 .github/workflows/

# build both packages' sdists+wheels into dist/
build:
    uv build --package effaced --out-dir dist
    uv build --package effaced-stripe --out-dir dist

# regenerate the API reference (griffe → MDX) into site/src/content/docs/docs/reference/
site-gen:
    uv run python scripts/gen_api_docs.py

# install site dependencies (Astro + Starlight via pnpm)
site-install:
    cd site && pnpm install

# docs/marketing dev server (regenerates the API reference first)
site-dev: site-gen
    cd site && pnpm dev

# production site build into site/dist/ (regenerates the API reference first)
site-build: site-gen
    cd site && pnpm build

# serve the production build locally (respects the /effaced base path)
site-preview:
    cd site && pnpm preview

# compact repo state: branch, status, recent commits
st:
    @git branch --show-current
    @git status --short
    @git log --oneline -8

# format staged python, then commit signed (no tests) — for "just commit"
commit-fast msg:
    git diff --cached --name-only --diff-filter=ACM | grep '\.py$' | xargs -r uv run ruff format
    git diff --cached --name-only --diff-filter=ACM | grep '\.py$' | xargs -r git add
    git commit -s -m "{{msg}}"

# push branch + open PR against stage with type/area labels
pr-open title type area:
    git push -u origin HEAD
    gh pr create --base stage --title "{{title}}" --label "type:{{type}}" --label "area:{{area}}" --fill-verbose

# clean build/test artifacts
clean:
    rm -rf dist/ .pytest_cache/ .mypy_cache/ .ruff_cache/ .hypothesis/ htmlcov/ .coverage coverage.xml packages/effaced/mutants/
    find . -type d -name __pycache__ -prune -exec rm -rf {} +
