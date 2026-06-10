---
title: "Behaviour changes need tests; erasure/export need bleed and retention proofs"
scope: "file"
path:
  - "packages/effaced/tests/**"
  - "packages/effaced-stripe/tests/**"
severity_min: "high"
buckets: ["test-coverage"]
enabled: true
---
## Instructions
Every behaviour change needs a test. Changes to what gets deleted or exported
additionally need cross-subject-bleed proofs (subject A's data never appears in
subject B's export/erasure) and retention-preservation proofs (RETAIN fields
survive any plan) — property tests where the shape allows. Flag erasure/export
behaviour changes whose accompanying tests don't actually exercise bleed and
retention.

## Examples
### Bad example
```python
def test_erase_runs():
    plan = planner.plan(subject_a)
    assert plan is not None          # asserts nothing about bleed or retention
```
### Good example
```python
@given(schemas=annotated_schemas())
def test_export_never_bleeds_across_subjects(schemas):
    export_a = exporter.export(subject_a, schemas)
    assert all(row.subject_id == subject_a.id for row in export_a.rows)
```
