---
title: "One concept per file; the package supplies the namespace"
scope: "file"
path:
  - "packages/*/src/**"
severity_min: "medium"
buckets: ["architecture"]
enabled: true
---
## Instructions
Each source file holds one public concept (class, protocol, or enum) and is
named for it. The name match is read **with the package as namespace**: inside
a domain package, the file name may drop the package's own prefix —
`erasure/plan.py` → `ErasurePlan`, `erasure/planner.py` → `ErasurePlanner`,
`erasure/result.py` → `ErasureResult`, `consent/record.py` → `ConsentRecord`,
`audit/event.py` → `AuditEvent` are all canonical (this is the documented
preferred shape: packages over modules). Flag only files that hold multiple
unrelated public concepts, or whose name matches **neither** the bare class
name nor the package-qualified one. Do not request renames that re-duplicate
the package name into the file name.

## Examples
### Bad example
```python
# File: packages/effaced/src/effaced/erasure/stuff.py
class ErasureResult: ...     # name matches neither "stuff" nor "erasure stuff"
class OutboxEntry: ...       # second unrelated concept in the same file
```
### Good example
```python
# File: packages/effaced/src/effaced/erasure/result.py
class ErasureResult: ...     # package namespace + file name = the class
```
