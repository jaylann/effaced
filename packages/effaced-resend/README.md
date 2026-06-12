# effaced-resend

First-party [effaced](https://github.com/jaylann/effaced) resolver for
[Resend](https://resend.com): `ResendResolver` reaches the data
subject's contact record via Resend's contacts API.

- **Export (Art. 15):** the contact's `email`, `first_name`,
  `last_name`, and `unsubscribed` flag.
- **Erase (Art. 17):** hard-deletes the contact via
  `DELETE /contacts/{email}` — under Resend's global-contacts model the
  contact disappears from every segment at once.

```bash
uv add effaced effaced-resend
```

(`effaced-resend` is not on PyPI yet — until its first release, install
it straight from this repo:
`uv add "effaced-resend @ git+https://github.com/jaylann/effaced#subdirectory=packages/effaced-resend"`.)

```python
from effaced import ResolverRegistry, SubjectRef
from effaced_resend import ResendResolver

registry = ResolverRegistry()
registry.register(ResendResolver(api_key="re_..."))  # server-side only

# Refs of kind "resend" are routed to this resolver; the value is the
# contact's email address as stored in Resend.
ref = SubjectRef(kind="resend", value="subject@example.com")
```

Or settings-driven, alongside your other resolvers:

```python
from effaced import ResolverSpec
from effaced_resend import ResendResolver

ResolverSpec(
    name="resend",
    settings_keys=("RESEND_API_KEY",),
    build=lambda settings: ResendResolver(settings["RESEND_API_KEY"]),
)
```

## Subject references

The ref value is the contact's **email address** — Resend's contacts
API addresses contacts by email directly, so no contact id needs to be
stored. Two consequences worth knowing:

- The ref value is itself PII and lives in effaced's outbox rows by
  design (that's where refs live so the saga can retry); `ResolverError`
  messages never carry it.
- Pass the email **as stored in Resend**. The resolver sends it
  verbatim (percent-encoded into a single path segment); it does not
  normalize case.

## What is exported

| Resend field | Export field | Category |
|---|---|---|
| `email` | `contact.email` | `CONTACT` |
| `first_name` | `contact.first_name` | `IDENTITY` |
| `last_name` | `contact.last_name` | `IDENTITY` |
| `unsubscribed` | `contact.unsubscribed` | `BEHAVIORAL` |

Empty or absent fields are skipped, not exported as noise. The custom
`properties` blob is **never exported**: its contents are caller-defined
and unknowable to this resolver — if you push PII into contact
properties, declare it in your own data map.

`ResendResolver.covered_surface` (the `AttestingResolver` capability)
declares these four fields — built from the same field tuple the
exporter uses, so the two cannot drift — and excludes
`contact.properties.*` with a reason. The shared conformance suite proves
every export stays within the declared surface and never touches the
exclusion. It declares *claimed* reach; it cannot prove Resend holds no
personal data elsewhere, and is not a compliance determination.

## What erasure does — and does not — cover

`erase_subject` deletes the **contact record**. It does not and cannot
touch:

- **Send history.** Resend retains records of sent emails (including
  recipient addresses) with no public deletion API; retention is a
  team-level dashboard setting. That data is part of your data map, not
  this resolver's.
- **Suppression state.** Deleting the contact also deletes its
  `unsubscribed` flag. If the subject is later re-added, their opt-out
  is gone — the flag is exported prominently so you can carry it into
  your own suppression list before erasing.

Erasing a contact Resend no longer knows reports
`already_absent=True` — success, never an error (the idempotency
contract effaced's saga retries depend on).

## Rectification

`ResendResolver` does not implement `rectify_subject`: Resend's update
endpoint cannot change a contact's `email` (the one `CONTACT` field),
and the name is split into `first_name`/`last_name`, so a single
category-keyed `IDENTITY` correction has no unambiguous target.
Resolvers without rectification are skipped and recorded during
rectification runs — never an error.

## Error taxonomy

| Response | Treatment |
|---|---|
| 2xx | Success. |
| 404 | Absent subject — empty export / `already_absent=True`. |
| 4xx except 404 and 429 | `ResolverError` — retrying cannot succeed. |
| 429, 5xx | Propagate (`httpx.HTTPStatusError`) — the saga runner retries. |
| Connection faults | Propagate (`httpx.TransportError`) — likewise retried. |

## Not legal advice

effaced ships mechanisms — tested machinery for Art. 15 export and
Art. 17 erasure of Resend-held contact data, and an auditable record
that you ran it. Whether your overall processing is lawful is a
determination only you (and your counsel) can make.
