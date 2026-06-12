/**
 * Roadmap content — single source of truth for /roadmap.
 *
 * When an item ships, flip its `status` here in the same PR
 * (stale guidance is a bug). No dates, ever: tiers express order
 * and commitment level, not schedule.
 */

export type RoadmapStatus = 'shipped' | 'in-progress' | 'planned' | 'exploring';

export interface RoadmapItem {
  name: string;
  /** One line, wording-discipline reviewed: mechanisms, never compliance claims. */
  scope: string;
  status: RoadmapStatus;
  /** GitHub issue/PR this item tracks. */
  ref?: { kind: 'issue' | 'pr'; number: number };
  /** Mono eyebrow, e.g. 'ART. 16'. Falls back to '#<ref>' when absent. */
  art?: string;
  /** Internal docs link (base-relative path like 'project/stability/'). */
  docsPath?: string;
  /** Visually de-emphasized (used for the hosted-ledger exploration). */
  faint?: boolean;
}

export interface RoadmapTier {
  id: 'now' | 'next' | 'later' | 'exploring';
  label: string;
  sub: string;
  note?: string;
  items: RoadmapItem[];
}

export const STATUS_LABEL: Record<RoadmapStatus, string> = {
  shipped: 'SHIPPED',
  'in-progress': 'IN PROGRESS',
  planned: 'PLANNED',
  exploring: 'EXPLORING',
};

export const tiers: RoadmapTier[] = [
  {
    id: 'now',
    label: 'NOW',
    sub: 'v0.1.0 · pre-alpha',
    note: 'The ruthlessly narrow first release: four mechanisms, one resolver, proven by a property-based and fault-injection suite.',
    items: [
      {
        name: 'Declarative data map',
        scope:
          'Annotate the SQLAlchemy models you already have — PII categories, subject links, retention. The annotations are the manifest.',
        status: 'shipped',
        docsPath: 'concepts/annotations/',
      },
      {
        name: 'Export',
        scope:
          'One structured bundle of everything a subject touches — local tables and resolvers. Failures are recorded, never silently dropped.',
        status: 'shipped',
        art: 'ART. 15',
        docsPath: 'concepts/export/',
      },
      {
        name: 'Erasure saga',
        scope:
          'FK-safe delete or anonymize, retained records skipped with the reason recorded, external deletions enqueued durably in the same transaction.',
        status: 'shipped',
        art: 'ART. 17',
        docsPath: 'concepts/erasure/',
      },
      {
        name: 'Consent ledger',
        scope:
          'Versioned, timestamped, append-only consent records — withdrawing is exactly as easy as granting.',
        status: 'shipped',
        art: 'ART. 7',
        docsPath: 'concepts/consent/',
      },
      {
        name: 'Append-only audit trail',
        scope:
          'Every export, erasure, and consent change leaves a PII-free record. No update or delete surface exists to misuse.',
        status: 'shipped',
        art: 'ART. 5(2)',
        docsPath: 'concepts/audit/',
      },
      {
        name: 'Stripe resolver',
        scope:
          'First first-party resolver — billing PII reached, erased idempotently, and conformance-tested.',
        status: 'shipped',
        docsPath: 'guides/stripe/',
      },
      {
        name: 'Completeness linter',
        scope:
          'Flags columns that look like PII but were never annotated — your unavoidable responsibility, made visible and CI-gateable.',
        status: 'shipped',
        docsPath: 'concepts/completeness/',
      },
      {
        name: 'Saga operator surface',
        scope: 'Read-only visibility into the outbox: what is pending, retrying, abandoned.',
        status: 'in-progress',
        ref: { kind: 'pr', number: 53 },
      },
      {
        name: 'Release 0.1.0 to PyPI',
        scope: 'First tagged pre-alpha through the automated release pipeline.',
        status: 'in-progress',
        ref: { kind: 'issue', number: 20 },
      },
    ],
  },
  {
    id: 'next',
    label: 'NEXT',
    sub: 'v0.2.0 · demand-pulled',
    note: 'Seeded from the 0.1.0 retro. Items get pulled when real usage demands them — and dropped when nobody asks.',
    items: [
      {
        name: 'S3 resolver',
        scope: 'Erase and export subject-owned objects in S3 buckets.',
        status: 'shipped',
        ref: { kind: 'issue', number: 45 },
      },
      {
        name: 'Supabase Auth resolver',
        scope: 'Reach the subject’s auth.users record via the Admin API — idempotent, conformance-tested.',
        status: 'shipped',
        ref: { kind: 'issue', number: 56 },
      },
      {
        name: 'Supabase Storage resolver',
        scope: 'Subject-owned storage objects, sharing machinery with the S3 resolver.',
        status: 'shipped',
        ref: { kind: 'issue', number: 57 },
      },
      {
        name: 'Rectification mechanism',
        scope: 'Correct a subject’s data across the mapped schema, auditably.',
        status: 'shipped',
        art: 'ART. 16',
        ref: { kind: 'issue', number: 46 },
        docsPath: 'concepts/rectification/',
      },
      {
        name: 'Restriction of processing',
        scope: 'Mark a subject restricted and make that state queryable and auditable.',
        status: 'shipped',
        art: 'ART. 18',
        ref: { kind: 'issue', number: 47 },
        docsPath: 'concepts/restriction/',
      },
      {
        name: 'Retention-expiry sweep',
        scope:
          'Surface records whose declared retention window has lapsed — report-only and audited, per ADR 0012.',
        status: 'shipped',
        ref: { kind: 'issue', number: 48 },
        docsPath: 'concepts/retention/',
      },
      {
        name: 'Settings-driven resolver registration',
        scope:
          'Build the resolver registry from configuration — registration stays explicit and auditable, just declarative.',
        status: 'shipped',
        ref: { kind: 'issue', number: 58 },
        docsPath: 'guides/settings-registration/',
      },
      {
        name: 'Requeue of abandoned outbox entries',
        scope: 'Operator API to safely re-run an abandoned external deletion, per ADR 0015.',
        status: 'shipped',
        ref: { kind: 'issue', number: 90 },
      },
      {
        name: 'Retention-only erasure',
        scope:
          'Systems with no per-subject delete (recordings, transcripts, vendor retention windows): schedule expiry, audit the horizon honestly, verify after it passes — per ADR 0018.',
        status: 'shipped',
        ref: { kind: 'issue', number: 107 },
        docsPath: 'concepts/retention/',
      },
      {
        name: 'Versioned docs + custom domain',
        scope: 'Docs that match the release you actually run; custom domain still ahead.',
        status: 'in-progress',
        ref: { kind: 'issue', number: 49 },
      },
    ],
  },
  {
    id: 'later',
    label: 'LATER',
    sub: 'on the road to 1.0',
    note: 'v1.0.0 is a stability promise, not a feature list — the gate is a real DSAR-style export and erasure executed and audited end-to-end in a production system.',
    items: [
      {
        name: 'Production dogfood DSAR',
        scope:
          'A real subject exported and erased in a real product, with the audit trail to show for it. The 1.0 gate.',
        status: 'planned',
        ref: { kind: 'issue', number: 51 },
      },
      {
        name: 'Backup-replay log',
        scope:
          'Backups resurrect erased subjects; replay the erasures committed since the backup point after a restore.',
        status: 'planned',
        ref: { kind: 'issue', number: 59 },
      },
      {
        name: 'Email-provider resolver',
        scope: 'Resend or SendGrid — whichever real users ask for first. Contact and suppression data is PII.',
        status: 'planned',
        ref: { kind: 'issue', number: 60 },
      },
      {
        name: 'Intercom resolver',
        scope: 'User profiles and conversation history, built on Intercom’s own deletion endpoint.',
        status: 'planned',
        ref: { kind: 'issue', number: 61 },
      },
      {
        name: 'Second ORM adapter',
        scope:
          'The core is already storage-agnostic; a second adapter proves it. Prisma/Drizzle or Django — demand decides which.',
        status: 'planned',
        ref: { kind: 'issue', number: 62 },
      },
      {
        name: '1.0 stability promise',
        scope:
          'Manifest format and resolver protocol stable enough to support for a year, under widened SemVer.',
        status: 'planned',
        docsPath: 'project/stability/',
      },
    ],
  },
  {
    id: 'exploring',
    label: 'EXPLORING',
    sub: 'scope notes, not commitments',
    note: 'No dates, no order. These graduate to a milestone only when real usage asks for them.',
    items: [
      {
        name: 'Framework-agnostic core',
        scope:
          'A language-neutral manifest + engine, so new languages become thin authoring layers over the same semantics instead of rewrites.',
        status: 'exploring',
        ref: { kind: 'issue', number: 63 },
      },
      {
        name: 'EU AI Act module',
        scope:
          'Event-logging (Art. 12) and AI-disclosure (Art. 50) mechanisms — the regulation’s transparency obligations apply from August 2026.',
        status: 'exploring',
        ref: { kind: 'issue', number: 64 },
      },
      {
        name: 'DSAR intake portal',
        scope:
          'The smallest hostable intake surface — request, verify, execute, evidence — without becoming a DSR platform.',
        status: 'exploring',
        ref: { kind: 'issue', number: 65 },
      },
      {
        name: 'Multi-app aggregation',
        scope: 'One subject, several of your products: aggregate exports, fan out erasure.',
        status: 'exploring',
        ref: { kind: 'issue', number: 66 },
      },
      {
        name: 'Hosted tamper-evident audit ledger',
        scope:
          'A possible commercial service on top of the audit trail — opaque identifiers and metadata only, never the rich PII. The library stays Apache-2.0, fully usable standalone.',
        status: 'exploring',
        faint: true,
      },
    ],
  },
];
