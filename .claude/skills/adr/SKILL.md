---
name: adr
description: Scaffold an Architectural Decision Record in docs/decisions/. Usage - /adr "title of the decision"
---

# /adr — record an architectural decision

1. Next number: `ls docs/decisions/ | sort | tail -1` → NNNN+1 (zero-padded 4 digits).
2. Create `docs/decisions/NNNN-kebab-case-title.md`:

```markdown
# NNNN. <Title>

- **Status:** accepted
- **Date:** <YYYY-MM-DD>

## Context

<What forces are at play; what problem this answers.>

## Decision

<What we will do, stated as a decision.>

## Consequences

<What becomes easier, what becomes harder, what we gave up.>
```

3. Reference the ADR from any rule/doc it affects. Decisions that change erasure/export semantics also need the widened-SemVer treatment (see `.claude/rules/gdpr-semantics.md`).
