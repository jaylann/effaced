# Security Policy

**An erasure bug is a data-protection bug.** If effaced deletes the wrong subject's data, exports one subject's data to another, fails to delete what it claims to have deleted, or mutates the audit trail — treat it as a security vulnerability and report it privately, even if it doesn't look like classic "security".

## Reporting

- Preferred: [GitHub Security Advisories](https://github.com/jaylann/effaced/security/advisories/new) (private)
- Email: Justin@Lanfermann.dev

Please do not open public issues for vulnerabilities. You'll get an acknowledgement within 72 hours and a fix timeline after triage.

## Supported versions

| Version | Supported |
|---|---|
| Latest release on PyPI (per package) | ✅ |
| Older releases | ❌ — upgrade; erasure semantics may have been fixed |

## What counts as a vulnerability here

- Cross-subject data bleed in export or erasure
- Deletion of legally retained data, or retention of data declared deletable
- Audit-trail gaps, mutations, or events that can be silently dropped
- Non-idempotent resolver behaviour that double-executes external effects
- Classic issues: injection, secrets leakage, dependency CVEs

## Disclosure

Fixes ship in a patch release with a prominent **Security** section in the changelog — loudly, not buried. Credits given unless you prefer otherwise.
