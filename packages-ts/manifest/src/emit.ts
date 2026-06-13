/**
 * serializeManifest: turn an author-facing DataMapManifest into the exact
 * snake_case JSON payload Python DataMap.from_payload accepts.
 *
 * Contract obligations (issue #62 spike):
 *   - Stamp schema_version = MANIFEST_SCHEMA_VERSION (2). See
 *     packages/effaced/src/effaced/manifest/migration.py.
 *   - NEVER emit a key Python does not define: every model is frozen with
 *     extra="forbid", so an unknown key is fatal under from_payload. We omit
 *     optional fields entirely rather than sending null where Python has no
 *     such field (null IS accepted for the nullable fields; we send it only
 *     for those).
 *   - Enforce the retain ⇒ retention invariant, mirroring the model_validator
 *     in annotations/pii_spec.py: erasure "retain" without a retention policy
 *     throws here, before any payload escapes.
 *   - RetentionPolicy.duration is a Python timedelta on the wire it is an
 *     ISO-8601 duration string. The author passes the string directly (e.g.
 *     "P30D"); we validate its shape so a seconds-number does not slip through
 *     and 422 inside Python.
 */
import type {
  ColumnEntry,
  DataMapManifest,
  PiiSpec,
  RetentionPolicy,
  SubjectLink,
  TableEntry,
} from "./types.js";

/** Current manifest schema version. Mirrors MANIFEST_SCHEMA_VERSION (Python). */
export const MANIFEST_SCHEMA_VERSION = 2 as const;

/** Thrown when the author-facing manifest violates a wire-contract invariant. */
export class ManifestEmitError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ManifestEmitError";
  }
}

/**
 * Loose ISO-8601 duration check (P[n]Y[n]M[n]D[T[n]H[n]M[n]S], weeks P[n]W).
 * Deliberately permissive: Python pydantic parses a broad ISO-8601 grammar.
 * We only reject obvious non-duration inputs (bare numbers, empty strings).
 */
const ISO8601_DURATION =
  /^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?!$)(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?$/;

function assertIsoDuration(value: string): void {
  if (!ISO8601_DURATION.test(value)) {
    throw new ManifestEmitError(
      `RetentionPolicy.duration must be an ISO-8601 duration string ` +
        `(e.g. "P30D", "P10Y"), got ${JSON.stringify(value)}. ` +
        `Python serializes timedelta as ISO-8601, never seconds.`,
    );
  }
}

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };
type JsonObject = { [key: string]: JsonValue };

function assertNonEmpty(value: string, field: string): void {
  if (value.length === 0) {
    throw new ManifestEmitError(
      `${field} must be a non-empty string — Python declares it ` +
        `Field(min_length=1); an empty value would only 422 downstream in ` +
        `from_payload, not here.`,
    );
  }
}

function emitRetention(policy: RetentionPolicy): JsonObject {
  assertNonEmpty(policy.reason, "RetentionPolicy.reason");
  const out: JsonObject = { reason: policy.reason };
  if (policy.basis !== undefined) out.basis = policy.basis;
  if (policy.duration !== undefined && policy.duration !== null) {
    assertIsoDuration(policy.duration);
    out.duration = policy.duration;
  } else if (policy.duration === null) {
    out.duration = null;
  }
  if (policy.anchor !== undefined) out.anchor = policy.anchor;
  return out;
}

function emitSpec(spec: PiiSpec): JsonObject {
  if (spec.erasure === "retain" && (spec.retention === undefined || spec.retention === null)) {
    throw new ManifestEmitError(
      `ErasureStrategy "retain" requires a RetentionPolicy — a retention ` +
        `duty must name its legal reason (mirrors pii_spec.py validator).`,
    );
  }
  const out: JsonObject = { category: spec.category };
  if (spec.erasure !== undefined) out.erasure = spec.erasure;
  if (spec.retention !== undefined && spec.retention !== null) {
    out.retention = emitRetention(spec.retention);
  } else if (spec.retention === null) {
    out.retention = null;
  }
  if (spec.legalBasis !== undefined) out.legal_basis = spec.legalBasis;
  if (spec.purpose !== undefined) out.purpose = spec.purpose;
  if (spec.description !== undefined) out.description = spec.description;
  return out;
}

function emitColumn(column: ColumnEntry): JsonObject {
  return { name: column.name, spec: emitSpec(column.spec) };
}

function emitSubjectLink(link: SubjectLink): JsonObject {
  const out: JsonObject = { path: link.path };
  if (link.subjectIdColumn !== undefined) {
    assertNonEmpty(link.subjectIdColumn, "SubjectLink.subject_id_column");
    out.subject_id_column = link.subjectIdColumn;
  }
  return out;
}

function emitTable(table: TableEntry): JsonObject {
  const out: JsonObject = { name: table.name };
  if (table.subjectLink !== undefined && table.subjectLink !== null) {
    out.subject_link = emitSubjectLink(table.subjectLink);
  } else if (table.subjectLink === null) {
    out.subject_link = null;
  }
  if (table.columns !== undefined) {
    out.columns = table.columns.map(emitColumn);
  }
  return out;
}

/**
 * Serialize an author-facing manifest to the Python-accepted wire payload.
 *
 * @throws ManifestEmitError when a "retain" column lacks a retention policy,
 *   or a retention duration is not an ISO-8601 duration string.
 */
export function serializeManifest(manifest: DataMapManifest): JsonObject {
  return {
    tables: manifest.tables.map(emitTable),
    schema_version: MANIFEST_SCHEMA_VERSION,
  };
}
