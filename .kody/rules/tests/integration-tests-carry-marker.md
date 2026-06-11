---
title: "Integration tests carry the integration marker and read the DB URL from env"
scope: "file"
path:
  - "packages/effaced/tests/**"
  - "packages/effaced-stripe/tests/**"
severity_min: "medium"
buckets: ["test-coverage"]
enabled: true
---
## Instructions
Tests that need real Postgres carry `@pytest.mark.integration` and read
`EFFACED_TEST_DATABASE_URL`; everything else must run with no services. Flag a
test that opens a database connection without the marker (it would break the
default, service-free run) or that hardcodes a database URL.

## Examples
### Bad example
```python
def test_fk_ordering():               # no marker; default run has no Postgres
    engine = create_engine("postgresql://localhost/effaced")  # hardcoded url
    ...
```
### Good example
```python
@pytest.mark.integration
def test_fk_ordering():
    engine = create_engine(os.environ["EFFACED_TEST_DATABASE_URL"])
    ...
```
