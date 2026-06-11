---
title: "Every erasure/export outcome emits an audit event"
scope: "file"
path:
  - "packages/effaced/src/effaced/audit/**"
  - "packages/effaced/src/effaced/erasure/**"
  - "packages/effaced/src/effaced/saga/**"
severity_min: "high"
buckets: ["audit-integrity"]
enabled: true
---
## Instructions
Every outcome must be recorded: success, failure, retention skip, and retry
abandonment each emit their event. Flag code paths that can complete — return,
`continue`, swallow an exception, or hit an early `return` — without emitting the
event for that outcome. A silently dropped outcome is an audit-trail gap.

## Examples
### Bad example
```python
try:
    await resolver.erase(ref)
    await sink.append(success_event(ref))
except ResolverError:
    return  # failure path completes without an audit event
```
### Good example
```python
try:
    await resolver.erase(ref)
    await sink.append(success_event(ref))
except ResolverError as exc:
    await sink.append(failure_event(ref, exc))   # outcome recorded
    raise
```
