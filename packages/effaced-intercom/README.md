# effaced-intercom

First-party [effaced](https://github.com/jaylann/effaced) resolver for
[Intercom](https://www.intercom.com): `IntercomResolver` reaches the data
subject's contact profile and conversation metadata via Intercom's REST
API.

- **Export (Art. 15):** the contact's `email`, `name`, and `phone`, plus
  per-conversation metadata (`created_at`, `updated_at`, `state`) for
  every conversation the contact appears in.
- **Erase (Art. 17):** hard-deletes the contact via
  `DELETE /contacts/{id}`.

```bash
uv add effaced effaced-intercom
```

(`effaced-intercom` is not on PyPI yet — until its first release, install
it straight from this repo:
`uv add "effaced-intercom @ git+https://github.com/jaylann/effaced#subdirectory=packages/effaced-intercom"`.)

```python
from effaced import ResolverRegistry, SubjectRef
from effaced_intercom import IntercomResolver

registry = ResolverRegistry()
registry.register(IntercomResolver(access_token="dG9rOi..."))  # server-side only

# Refs of kind "intercom" are routed to this resolver; the value is the
# contact's Intercom internal id.
ref = SubjectRef(kind="intercom", value="5f7f0d217ef88b001234abcd")
```

Or settings-driven, alongside your other resolvers:

```python
from effaced import ResolverSpec
from effaced_intercom import IntercomResolver

ResolverSpec(
    name="intercom",
    settings_keys=("INTERCOM_ACCESS_TOKEN",),
    build=lambda settings: IntercomResolver(settings["INTERCOM_ACCESS_TOKEN"]),
)
```

## Subject references

The ref value is the contact's **Intercom internal id** (the `id` field
on a contact, e.g. `"5f7f0d217ef88b001234abcd"`) — get and delete address
the contact by id directly, so the resolver never has to search by email
or enumerate. Resolve your subject's email to an Intercom contact id in
your own data map and pass the id here.

## What is exported

| Intercom field | Export field | Category |
|---|---|---|
| contact `email` | `contact.email` | `CONTACT` |
| contact `name` | `contact.name` | `IDENTITY` |
| contact `phone` | `contact.phone` | `CONTACT` |
| conversation `created_at` | `conversation.{id}.created_at` | `BEHAVIORAL` |
| conversation `updated_at` | `conversation.{id}.updated_at` | `BEHAVIORAL` |
| conversation `state` | `conversation.{id}.state` | `BEHAVIORAL` |

Empty or absent fields are skipped, not exported as noise. Two things are
**never exported**:

- **Conversation message bodies.** Only interaction metadata (timestamps,
  state) is collected — the replies and notes inside a conversation
  (`conversation_parts`, `source.body`) are deep content this resolver
  deliberately leaves untouched.
- **The custom `custom_attributes` blob.** Its contents are
  caller-defined and unknowable to this resolver — if you push PII into
  custom attributes, declare it in your own data map.

`IntercomResolver.covered_surface` (the `AttestingResolver` capability)
declares these fields — built from the same field tuples the exporter
uses, so the two cannot drift — and excludes
`contact.custom_attributes.*` and `conversation.*.conversation_parts.*`
with reasons. The shared conformance suite proves every export stays
within the declared surface and never touches an exclusion. It declares
*claimed* reach; it cannot prove Intercom holds no personal data
elsewhere, and is not a compliance determination.

## What erasure does — and does not — cover

`erase_subject` deletes the **contact record** by id. It does not and
cannot separately delete the conversation records the contact appears in:
their retention is governed by Intercom's own settings, not this
resolver. That conversation data is part of your data map — export
collects its metadata so you can account for it.

Erasing a contact Intercom no longer knows reports
`already_absent=True` — success, never an error (the idempotency
contract effaced's saga retries depend on).

## Rectification

`IntercomResolver` does not implement `rectify_subject` in this first
cut. Resolvers without rectification are skipped and recorded during
rectification runs — never an error.

## Error taxonomy

| Response | Treatment |
|---|---|
| 2xx | Success. |
| 404 | Absent subject — empty export / `already_absent=True`. |
| 4xx except 404 and 429 | `ResolverError` — retrying cannot succeed. |
| 429, 5xx | Propagate (`httpx.HTTPStatusError`) — the saga runner retries. |
| Connection faults | Propagate (`httpx.TransportError`) — likewise retried. |

`ResolverError` messages carry the status and a short action verb only —
never the contact id or the access token.

## Not legal advice

effaced ships mechanisms — tested machinery for Art. 15 export and
Art. 17 erasure of Intercom-held contact data, and an auditable record
that you ran it. Whether your overall processing is lawful is a
determination only you (and your counsel) can make.
