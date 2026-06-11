---
title: "Removing or renaming a manifest enum member is a format change"
scope: "file"
path:
  - "packages/effaced/src/effaced/manifest/**"
  - "packages/effaced/src/effaced/annotations/**"
  - "packages/effaced/src/effaced/categories/**"
severity_min: "high"
buckets: ["manifest-format", "breaking-change"]
enabled: true
---
## Instructions
Removing or renaming a member of `PiiCategory`, `LegalBasis`, or `ErasureStrategy`
is a manifest format change → breaking, because persisted manifests reference the
member by value. Adding a member is additive. Flag removal/rename of an existing
member without a breaking declaration.

## Examples
### Bad example
```python
class PiiCategory(StrEnum):
    CONTACT = "contact"
    # BIOMETRIC removed — any stored manifest referencing it now fails to load
```
### Good example
```python
class PiiCategory(StrEnum):
    CONTACT = "contact"
    BIOMETRIC = "biometric"
    GENETIC = "genetic"   # additive
```
