/**
 * Hand-mirrored TypeScript types for the effaced cross-language manifest wire
 * contract (issue #62 spike).
 *
 * SOURCE OF TRUTH: these interfaces and unions mirror the Python pydantic
 * models by hand. There is NO JSON Schema export in Python today, so nothing
 * generates them. The enum-drift guard in test/emit.test.ts and the
 * round-trip test in test/roundtrip.test.ts are the ONLY contract enforcement.
 * Keep these in lockstep with:
 *
 *   - PiiCategory      packages/effaced/src/effaced/categories/pii_category.py
 *   - ErasureStrategy  packages/effaced/src/effaced/categories/erasure_strategy.py
 *   - LegalBasis       packages/effaced/src/effaced/categories/legal_basis.py
 *   - PiiSpec          packages/effaced/src/effaced/annotations/pii_spec.py
 *   - RetentionPolicy  packages/effaced/src/effaced/annotations/retention_policy.py
 *   - SubjectLink      packages/effaced/src/effaced/annotations/subject_link.py
 *   - ColumnEntry      packages/effaced/src/effaced/manifest/column_entry.py
 *   - TableEntry       packages/effaced/src/effaced/manifest/table_entry.py
 *   - DataMap          packages/effaced/src/effaced/manifest/data_map.py
 *   - schema version   packages/effaced/src/effaced/manifest/migration.py
 *
 * Every Python manifest model is frozen with extra="forbid". A key these types
 * emit that Python does not define is FATAL under DataMap.from_payload.
 * Optional fields may be omitted (Python supplies defaults).
 */

/** Mirrors PiiCategory (categories/pii_category.py). */
export type PiiCategory =
  | "contact"
  | "identity"
  | "financial"
  | "behavioral"
  | "technical"
  | "location"
  | "communication"
  | "special";

/** Mirrors ErasureStrategy (categories/erasure_strategy.py). */
export type ErasureStrategy = "delete" | "anonymize" | "retain";

/** Mirrors LegalBasis (categories/legal_basis.py). */
export type LegalBasis =
  | "consent"
  | "contract"
  | "legal_obligation"
  | "vital_interests"
  | "public_task"
  | "legitimate_interests";

/**
 * Mirrors RetentionPolicy (annotations/retention_policy.py).
 *
 * CROSS-LANGUAGE HAZARD: duration is a Python timedelta, serialized by pydantic
 * as an ISO-8601 duration STRING (e.g. "P30D", "P10Y"), never a number of
 * seconds. serializeManifest produces that string; here the wire field is the
 * already-serialized string.
 */
export interface RetentionPolicy {
  /** Human-readable legal duty. Python Field(min_length=1). */
  reason: string;
  /** Defaults to "legal_obligation" in Python; omit to accept that default. */
  basis?: LegalBasis;
  /** ISO-8601 duration string ("P30D"); null/omitted = indefinite. */
  duration?: string | null;
  /** Datetime column on the same table; null/omitted = no clock. */
  anchor?: string | null;
}

/** Mirrors PiiSpec (annotations/pii_spec.py). */
export interface PiiSpec {
  category: PiiCategory;
  /** Defaults to "delete" in Python; omit to accept that default. */
  erasure?: ErasureStrategy;
  /** Required when erasure === "retain" (validator-enforced both sides). */
  retention?: RetentionPolicy | null;
  legalBasis?: LegalBasis | null;
  purpose?: string | null;
  description?: string | null;
}

/** Mirrors SubjectLink (annotations/subject_link.py). */
export interface SubjectLink {
  /** Dotted relationship path; "" marks the subject table itself. */
  path: string;
  /** Defaults to "id" in Python; omit to accept that default. */
  subjectIdColumn?: string;
}

/** Mirrors ColumnEntry (manifest/column_entry.py). */
export interface ColumnEntry {
  name: string;
  spec: PiiSpec;
}

/** Mirrors TableEntry (manifest/table_entry.py). */
export interface TableEntry {
  name: string;
  subjectLink?: SubjectLink | null;
  columns?: ColumnEntry[];
}

/**
 * Author-facing manifest shape consumed by serializeManifest. This is the
 * camelCase TS-ergonomic input; the emitter maps it to the snake_case wire
 * payload Python expects. schema_version is stamped by the emitter.
 */
export interface DataMapManifest {
  tables: TableEntry[];
}
