<!-- @kody-sync -->
# Manifest format (packages/effaced/src/effaced/manifest/, annotations/, categories/)

- Serialized-format changes require a `MANIFEST_SCHEMA_VERSION` bump plus an explicit forward-migration branch in `migration.py`. Old manifests are migrated, never rejected.
- Enum member removal/rename (`PiiCategory`, `LegalBasis`, `ErasureStrategy`) is a format change → breaking.
- Domain models stay frozen pydantic with `extra="forbid"`; flag `model_construct()` in production code (bypasses validators).
- Round-trip fidelity: `DataMap.from_payload(m.to_payload()) == m` must hold; flag serialization changes without a matching property-test update.
