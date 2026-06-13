/**
 * @effaced/manifest — TS spike emitter for the cross-language manifest wire
 * contract. Emit + round-trip only; no erasure/export/resolution. See README.
 */
export * from "./types.js";
export { MANIFEST_SCHEMA_VERSION, ManifestEmitError, serializeManifest } from "./emit.js";
