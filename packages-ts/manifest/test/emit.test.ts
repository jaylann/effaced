import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { serializeManifest, ManifestEmitError, MANIFEST_SCHEMA_VERSION } from "../src/index.js";
import type {
  DataMapManifest,
  ErasureStrategy,
  LegalBasis,
  PiiCategory,
} from "../src/index.js";
import { sampleManifest } from "./fixtures/sample_manifest.js";

const here = dirname(fileURLToPath(import.meta.url));
// packages-ts/manifest/test -> repo root is four levels up.
const repoRoot = resolve(here, "..", "..", "..");
const categoriesDir = resolve(
  repoRoot,
  "packages/effaced/src/effaced/categories",
);

/**
 * Extract the string VALUES of a Python StrEnum from its source file by
 * scanning `NAME = "value"` assignments. The enum files are flat StrEnum
 * declarations (one member per line) so a line regex is sufficient and avoids
 * needing a Python runtime in the default test job.
 */
function pythonEnumValues(file: string): string[] {
  const text = readFileSync(resolve(categoriesDir, file), "utf8");
  const values: string[] = [];
  for (const line of text.split("\n")) {
    // tolerate a trailing comment after the value, so a future member written
    // `MEMBER = "value"  # note` is still captured rather than silently skipped.
    const match = /^\s+[A-Z_]+\s*=\s*"([^"]+)"\s*(#.*)?$/.exec(line);
    if (match && match[1] !== undefined) values.push(match[1]);
  }
  return values;
}

describe("enum-drift guard (TS unions vs Python StrEnums)", () => {
  // The TS string-literal unions are not introspectable at runtime, so we
  // assert exhaustiveness by listing every member the union claims and letting
  // the type-checker reject a member the union does not include. If Python
  // gains/loses an enum value, these sets diverge and the test fails.
  it("PiiCategory matches pii_category.py", () => {
    const tsCategories: PiiCategory[] = [
      "contact",
      "identity",
      "financial",
      "behavioral",
      "technical",
      "location",
      "communication",
      "special",
    ];
    expect([...tsCategories].sort()).toEqual(
      pythonEnumValues("pii_category.py").sort(),
    );
  });

  it("ErasureStrategy matches erasure_strategy.py", () => {
    const tsStrategies: ErasureStrategy[] = ["delete", "anonymize", "retain"];
    expect([...tsStrategies].sort()).toEqual(
      pythonEnumValues("erasure_strategy.py").sort(),
    );
  });

  it("LegalBasis matches legal_basis.py", () => {
    const tsBases: LegalBasis[] = [
      "consent",
      "contract",
      "legal_obligation",
      "vital_interests",
      "public_task",
      "legitimate_interests",
    ];
    expect([...tsBases].sort()).toEqual(
      pythonEnumValues("legal_basis.py").sort(),
    );
  });
});

describe("serializeManifest", () => {
  it("stamps the schema version and snake_cases the wire payload", () => {
    expect(serializeManifest(sampleManifest)).toMatchInlineSnapshot(`
      {
        "schema_version": 2,
        "tables": [
          {
            "columns": [
              {
                "name": "email",
                "spec": {
                  "category": "contact",
                  "erasure": "delete",
                  "legal_basis": "consent",
                  "purpose": "account login and notifications",
                },
              },
              {
                "name": "full_name",
                "spec": {
                  "category": "identity",
                  "erasure": "anonymize",
                },
              },
            ],
            "name": "users",
            "subject_link": {
              "path": "",
            },
          },
          {
            "columns": [
              {
                "name": "billing_address",
                "spec": {
                  "category": "financial",
                  "erasure": "retain",
                  "legal_basis": "legal_obligation",
                  "purpose": "statutory invoice retention",
                  "retention": {
                    "anchor": "invoiced_at",
                    "basis": "legal_obligation",
                    "duration": "P10Y",
                    "reason": "section 147 AO invoice retention",
                  },
                },
              },
            ],
            "name": "invoices",
            "subject_link": {
              "path": "user",
              "subject_id_column": "id",
            },
          },
        ],
      }
    `);
  });

  it("uses MANIFEST_SCHEMA_VERSION for the stamp", () => {
    const payload = serializeManifest({ tables: [] });
    expect(payload.schema_version).toBe(MANIFEST_SCHEMA_VERSION);
  });

  it("throws when a retain column has no retention policy", () => {
    const bad: DataMapManifest = {
      tables: [
        {
          name: "ledger",
          columns: [{ name: "amount", spec: { category: "financial", erasure: "retain" } }],
        },
      ],
    };
    expect(() => serializeManifest(bad)).toThrow(ManifestEmitError);
  });

  it("throws when a duration is not an ISO-8601 duration string", () => {
    const bad: DataMapManifest = {
      tables: [
        {
          name: "ledger",
          columns: [
            {
              name: "amount",
              spec: {
                category: "financial",
                erasure: "retain",
                retention: { reason: "x", duration: "2592000" },
              },
            },
          ],
        },
      ],
    };
    expect(() => serializeManifest(bad)).toThrow(/ISO-8601/);
  });

  it("never emits an undefined optional field as a key", () => {
    const payload = serializeManifest({
      tables: [{ name: "t", columns: [{ name: "c", spec: { category: "technical" } }] }],
    });
    const tables = payload.tables as Record<string, unknown>[];
    const table = tables[0];
    if (table === undefined) throw new Error("expected one table");
    const columns = table.columns as Record<string, unknown>[];
    const col = columns[0];
    if (col === undefined) throw new Error("expected one column");
    const colSpec = col.spec as Record<string, unknown>;
    expect(Object.keys(colSpec)).toEqual(["category"]);
  });
});
