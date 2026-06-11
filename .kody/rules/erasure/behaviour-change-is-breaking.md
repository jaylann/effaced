---
title: "A change to what gets deleted or exported is a breaking change"
scope: "file"
path:
  - "packages/effaced/src/effaced/erasure/**"
  - "packages/effaced/src/effaced/saga/**"
  - "packages/effaced/src/effaced/export/**"
  - "packages/effaced/src/effaced/resolvers/**"
severity_min: "critical"
buckets: ["breaking-change", "erasure-semantics"]
enabled: true
---
## Instructions
Any change to WHAT gets deleted or exported is breaking, even when the function
signature is identical — require the `breaking` label and an explicit declaration
in the PR body. Flag undeclared behaviour changes (a field newly deleted, a field
no longer deleted, a row newly included in or dropped from an export) as blockers.

## Examples
### Bad example
```python
# Silently starts erasing a field that was previously left intact,
# in a PR with no `breaking` label and no PR-body declaration.
def fields_to_erase(spec: PiiSpec) -> list[str]:
    return [*spec.erasable_fields, "last_login_ip"]  # newly deleted, undeclared
```
### Good example
```python
# Same change, but declared: PR carries the `breaking` label and the body
# states "now also erases last_login_ip (widened erasure -> MAJOR)".
def fields_to_erase(spec: PiiSpec) -> list[str]:
    return [*spec.erasable_fields, "last_login_ip"]
```
