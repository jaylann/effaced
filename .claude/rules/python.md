---
paths: ["**/*.py"]
---

# Python standards

## Typing — strict, everywhere
- mypy runs `--strict` with the pydantic plugin (`disallow_any_unimported`, `strict_equality`, `extra_checks` on top). It must stay at zero errors.
- No `Any` where a real type exists. `object` for "truly anything JSON-ish" beats `Any`.
- `# type: ignore` requires the error code AND an inline reason: `# type: ignore[type-arg]  # sessionmaker generic unbound here`.
- Ruff `ANN` rules enforce annotations syntactically; don't suppress them outside tests.
- `from __future__ import annotations` at the top of every module.

## Pydantic-first data modeling
- Domain data objects are pydantic `BaseModel` with `model_config = ConfigDict(frozen=True, extra="forbid")`.
- Cross-field invariants live in `model_validator(mode="after")`; single-field constraints in `Field(...)` (e.g. `min_length=1`). Never validate in call sites.
- Never use `model_construct()` in production code — it bypasses validators.
- Enums are `StrEnum` so they serialize as their value.

## File & folder architecture — small and searchable
- **One concept per file.** The file is named after the class it holds: `pii_spec.py` → `PiiSpec`, `data_map.py` → `DataMap`.
- A new class/protocol/enum of public relevance gets its own file, re-exported from the package `__init__.py` (which holds docstring + re-exports ONLY, no logic).
- Hard cap **600 lines per source file**, enforced by `scripts/check_file_length.py` in `just check` and CI. If you approach it, split into a package.
- Prefer packages over modules: `erasure/{plan,planner,result}.py`, not one `erasure.py`.
- Absolute imports only (ruff TID bans relative imports).
- Complexity budgets are CI-enforced (mccabe ≤ 10, pylint max-args 6 / branches 10 / statements 40). Don't suppress; restructure.

## Misc
- Docstrings: Google convention on every public module/class/function (ruff `D`). They are the future generated docs site — write them as documentation.
- Core (`effaced/*` except `adapters/`) must not import SQLAlchemy or any storage library; storage-specific code lives in `effaced/adapters/<stack>/`.
- The PostToolUse hook auto-formats edited files — but it also auto-removes imports that are momentarily unused; when adding an import for code you haven't written yet, write the usage in the same edit.
