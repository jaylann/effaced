import { type SpawnSyncReturns, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { serializeManifest } from "../src/index.js";
import { fullyPopulatedManifest, sampleManifest } from "./fixtures/sample_manifest.js";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");

/** Run a Python snippet against the workspace; returns the completed process. */
function runPython(script: string, input: string): SpawnSyncReturns<string> {
  return spawnSync("uv", ["run", "--quiet", "python", "-c", script], {
    input,
    cwd: repoRoot,
    encoding: "utf8",
  });
}

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
    const result = runPython(script, payload);
    if (result.status !== 0) {
      throw new Error(`round-trip failed (exit ${result.status}):\n${result.stderr ?? ""}`);
    }
    expect(result.status).toBe(0);
  });

  it("preserves every emitted field byte-for-byte (fidelity, not just acceptance)", () => {
    // Acceptance (exit 0) alone would pass even if Python silently dropped or
    // defaulted a field. Round-trip a fully-populated manifest through
    // from_payload → to_payload and assert the re-serialized payload equals
    // what we emitted: proof that no field was renamed, dropped, or altered.
    const emitted = serializeManifest(fullyPopulatedManifest);
    const script =
      "import json,sys; from effaced import DataMap; "
      + "print(json.dumps(DataMap.from_payload(json.load(sys.stdin)).to_payload()))";
    const result = runPython(script, JSON.stringify(emitted));
    if (result.status !== 0) {
      throw new Error(`round-trip failed (exit ${result.status}):\n${result.stderr ?? ""}`);
    }
    expect(JSON.parse(result.stdout)).toEqual(emitted);
  });
});
