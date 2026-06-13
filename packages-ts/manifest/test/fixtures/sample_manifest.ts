/**
 * Hand-authored sample manifest exercising the wire contract:
 *   - a subject table (subjectLink.path === "")
 *   - a relational child table reaching the subject via a dotted path
 *   - a RETAIN column WITH a retention policy (duration + anchor)
 *   - a plain DELETE column
 *
 * This is the input to serializeManifest in both the snapshot and round-trip
 * tests. It must stay accepted by Python DataMap.from_payload.
 */
import type { DataMapManifest } from "../../src/types.js";

export const sampleManifest: DataMapManifest = {
  tables: [
    {
      name: "users",
      subjectLink: { path: "" },
      columns: [
        {
          name: "email",
          spec: {
            category: "contact",
            erasure: "delete",
            legalBasis: "consent",
            purpose: "account login and notifications",
          },
        },
        {
          name: "full_name",
          spec: { category: "identity", erasure: "anonymize" },
        },
      ],
    },
    {
      name: "invoices",
      subjectLink: { path: "user", subjectIdColumn: "id" },
      columns: [
        {
          name: "billing_address",
          spec: {
            category: "financial",
            erasure: "retain",
            legalBasis: "legal_obligation",
            purpose: "statutory invoice retention",
            retention: {
              reason: "section 147 AO invoice retention",
              basis: "legal_obligation",
              duration: "P10Y",
              anchor: "invoiced_at",
            },
          },
        },
      ],
    },
  ],
};

/**
 * Every optional field set to a non-default value, so a Python round-trip
 * (from_payload → to_payload) re-serializes to a byte-identical payload — the
 * fidelity check that acceptance (exit 0) alone cannot give. The duration
 * `P10Y` is in pydantic's canonical ISO-8601 form (preserved verbatim, unlike
 * e.g. `P365D`, which normalizes to `P1Y`).
 */
export const fullyPopulatedManifest: DataMapManifest = {
  tables: [
    {
      name: "users",
      subjectLink: { path: "", subjectIdColumn: "id" },
      columns: [
        {
          name: "ledger_note",
          spec: {
            category: "financial",
            erasure: "retain",
            legalBasis: "legal_obligation",
            purpose: "statutory retention",
            description: "kept under tax law",
            retention: {
              reason: "section 147 AO",
              basis: "legal_obligation",
              duration: "P10Y",
              anchor: "created_at",
            },
          },
        },
      ],
    },
  ],
};
