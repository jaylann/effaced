# 0001. uv workspace monorepo with per-resolver packages

- **Status:** accepted
- **Date:** 2026-06-10

## Context

First-party resolvers (Stripe first; S3/Resend/Supabase later) must be installable separately so the core stays dependency-light — a user without Stripe should not install the Stripe SDK. Options: one package with extras, or a monorepo of real packages.

## Decision

uv workspace monorepo: `packages/effaced` (core) + `packages/effaced-stripe`, each its own PyPI distribution with its own version, changelog, and tag (`effaced-vX.Y.Z`, `effaced-stripe-vX.Y.Z` via release-please components). Resolver packages depend on `effaced` as a workspace source locally and a normal dependency when published.

## Consequences

- Resolvers version independently — a Stripe SDK bump never forces a core release.
- One repo, one CI, one issue tracker, shared tooling config at the workspace root.
- Adding a resolver = new `packages/effaced-<name>` + release-please entry + publish.yml tag mapping + PyPI trusted-publisher registration.
