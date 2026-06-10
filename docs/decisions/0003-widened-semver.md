# 0003. Widened SemVer: behaviour is API

- **Status:** accepted
- **Date:** 2026-06-10

## Context

For a compliance-mechanisms library, the worst failure is *silently changing what gets deleted or exported* — a user upgrades a patch release and their erasure quietly covers less (legal exposure) or more (destroys retained records). Classic SemVer only covers signatures.

## Decision

MAJOR includes: (1) API changes, (2) manifest-format changes, (3) **any change to what gets deleted or exported**. Old manifests are auto-migrated forward (`MANIFEST_SCHEMA_VERSION` + migration branches), never rejected. `Resolver`/`AuditSink` protocols evolve additively only. Deprecations get a runway (warning → window → removal in next major); nothing is yanked.

## Consequences

- PR template forces an explicit "Erasure/export semantics" declaration; reviewer agent + Kodus treat undeclared behaviour changes as blockers.
- More majors than a typical library — that is the honest price of the guarantee.
- Changelogs flag such changes under a prominent **Security** section.
