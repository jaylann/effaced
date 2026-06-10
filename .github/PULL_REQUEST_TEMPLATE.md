# Summary

<!-- 1–3 bullets: what changed and why. Link issues (Closes #12). -->

## Erasure/export semantics

<!-- REQUIRED: Does this change WHAT gets deleted or exported, for anyone?
     If yes: this is a MAJOR change — label it `breaking` and explain. If no: say "No". -->

## Risk

<!-- What could break? Rollback story? -->

## Checklist

- [ ] Targets `stage` (never `main`)
- [ ] PR title is Conventional Commits (`type(scope)?: lowercase subject`)
- [ ] All commits DCO signed-off (`git commit -s`)
- [ ] `just check` and `just test` green locally
- [ ] Tests added/updated — erasure/export paths prove no cross-subject bleed
- [ ] Public API has Google-style docstrings
- [ ] Manifest format untouched, or schema version bumped + migration added
- [ ] At least one `type:*` and relevant `area:*` labels set
