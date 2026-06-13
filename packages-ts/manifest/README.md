# @effaced/manifest (TypeScript spike)

> **Scope ceiling — read first.** This is an *exploratory spike*, not a product.
> It does exactly one thing: **emit a JSON manifest that Python effaced
> `DataMap.from_payload()` accepts**, and prove it round-trips. It does **NOT**
> erase, export, resolve, or talk to any database. It is **not** a Prisma or
> Drizzle adapter. There is no engine here and none is planned in this package.

It exists to pin down the cross-language *manifest wire contract* and surface
its gaps before we commit to a TypeScript adapter. Findings feed the future
contract ADRs. Tracking: issue #62. Blocked on #63 / #71 / #74 / #75.

## What it is

- `src/types.ts` — hand-mirrored TS interfaces + string-literal unions for the
  manifest models and the three category enums. **Hand-written**: Python has no
  JSON Schema export, so nothing generates these.
- `src/emit.ts` — `serializeManifest(manifest)` produces the snake_case wire
  payload, stamps `schema_version: 2`, enforces the `retain ⇒ retention`
  invariant (throws), and validates `RetentionPolicy.duration` as an ISO-8601
  duration string.
- `test/emit.test.ts` — JSON snapshot + an enum-drift guard that reads the
  Python enum source files and asserts the TS unions still match.
- `test/roundtrip.test.ts` — pipes the emitted JSON into Python
  `DataMap.from_payload` (exit 0 = contract holds). Gated behind
  `EFFACED_ROUNDTRIP=1` (skipped otherwise — needs `uv` + the Python workspace).

## Wire-contract findings (the point of the spike)

1. **No JSON Schema export in Python.** TS types are mirrored by hand. The only
   contract enforcement is the enum-drift guard + the live round-trip test. A
   real adapter needs a generated, single-source-of-truth schema (ADR #74/#75).
2. **`timedelta` → ISO-8601 duration string.** `RetentionPolicy.duration` is a
   Python `timedelta`; pydantic serializes it as `"P30D"`/`"P10Y"`, **never** a
   number of seconds. A naive emitter that sends a number 422s inside Python.
   `serializeManifest` enforces the string shape.
3. **`extra="forbid"` everywhere.** Every manifest pydantic model is
   `frozen=True, extra="forbid"`. Any key the emitter writes that Python does
   not define is **fatal** under `from_payload`. The emitter omits unset
   optional fields rather than guessing, and uses snake_case keys
   (`legal_basis`, `subject_id_column`).
4. **Enum-drift hazard.** The three vocabularies (`PiiCategory`,
   `ErasureStrategy`, `LegalBasis`) are manifest format; adding/removing a
   member silently desyncs a hand-mirrored TS client. The drift guard catches
   it in CI, but only because both languages live in one repo.

## Running

```bash
pnpm -C packages-ts install
pnpm -C packages-ts test          # snapshot + enum-drift guard (no Python needed)
EFFACED_ROUNDTRIP=1 pnpm -C packages-ts test   # also runs the Python round-trip
```

`pnpm -C packages-ts -r lint` runs `tsc --noEmit`.

## Not in scope

No erasure. No export. No resolver/saga. No DB access. No published package.
Mechanisms only — and this one only *describes* where personal data lives; it
makes no compliance determination.
