---
title: "Tests exercise public behaviour through the public API"
scope: "file"
path:
  - "packages/effaced/tests/**"
  - "packages/effaced-stripe/tests/**"
severity_min: "medium"
buckets: ["test-coverage"]
enabled: true
---
## Instructions
Test public behaviour through the public API. Flag tests that poke private
attributes (`_name`), call private methods, or assert on internal state instead
of observable behaviour — they couple the suite to implementation and let real
regressions pass.

## Examples
### Bad example
```python
def test_plan_internal():
    planner = ErasurePlanner(spec)
    assert planner._cache == {}          # private attribute
    assert planner._build_steps() == []  # private method
```
### Good example
```python
def test_plan_skips_retained_fields():
    plan = ErasurePlanner(spec).plan(ref)     # public API
    assert "created_at" not in plan.deleted_fields
```
