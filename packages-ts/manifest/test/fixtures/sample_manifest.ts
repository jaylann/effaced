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
