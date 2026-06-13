import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { serializeManifest } from "../src/index.js";
import { sampleManifest } from "./fixtures/sample_manifest.js";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");

/**
 * Live cross-language contract check: pipe the emitted JSON into Python and
 * assert DataMap.from_payload accepts it (exit 0). Gated behind
 * EFFACED_ROUNDTRIP=1 because it needs uv + the Python workspace; the default
 * `pnpm test` job (and CI lint job) skips it.
 */
const ROUNDTRIP = process.env.EFFACED_ROUNDTRIP === "1";

describe.skipIf(!ROUNDTRIP)("python round-trip (DataMap.from_payload)", () => {
  it("accepts the emitted sample manifest", () => {
    const payload = JSON.stringify(serializeManifest(sampleManifest));
    const script =
      "import json,sys; from effaced import DataMap; " +
      "DataMap.from_payload(json.load(sys.stdin))";
    const result = spawnSync("uv", ["run", "--quiet", "python", "-c", script], {
      input: payload,
      cwd: repoRoot,
      encoding: "utf8",
    });
    if (result.status !== 0) {
      throw new Error(
        `round-trip failed (exit ${result.status}):\n${result.stderr ?? ""}`,
      );
    }
    expect(result.status).toBe(0);
  });
});
