# 0005. Docs engine deferred; docstring discipline enforced now

- **Status:** accepted
- **Date:** 2026-06-10

## Context

effaced.dev will eventually serve `/docs` with auto-generated API reference matching the site's design. Candidates: **MkDocs Material + mkdocstrings** (Python-ecosystem standard; API ref from docstrings/type hints; gen-files for per-module pages; `mike` for versioned deploys; brandable via CSS tokens) vs **Astro Starlight** (pixel-identical with a future marketing site, but needs a Python→markdown generation bridge and a JS toolchain).

## Decision

Defer the engine choice until effaced.dev exists. Enforce the input discipline now so either generator works with zero rework: ruff `D` rules with the **Google convention** on all public API, docstrings written as documentation (contracts, guarantees, examples), reviewer agent flags undocumented public API.

## Consequences

- No docs infrastructure to maintain pre-launch; READMEs are the docs.
- When the site lands, the likely path is MkDocs Material + mkdocstrings + gen-files + mike, served under `effaced.dev/docs` via rewrite.
