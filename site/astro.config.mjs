// @ts-check
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import starlightVersions from 'starlight-versions';

// Deploy target is env-driven (ADR 0011): GitHub Pages today, effaced.dev later
// by setting SITE_URL=https://effaced.dev and BASE_PATH=/ in the deploy workflow.
const SITE_URL = process.env.SITE_URL ?? 'https://jaylann.github.io';
const BASE_PATH = process.env.BASE_PATH ?? '/effaced';

export default defineConfig({
  site: SITE_URL,
  base: BASE_PATH,
  integrations: [
    starlight({
      title: 'effaced',
      description:
        'GDPR data-subject mechanisms — Art. 15 export, Art. 17 erasure, Art. 7 consent, append-only audit — for your own database and external systems.',
      customCss: [
        '@fontsource-variable/public-sans',
        '@fontsource/ibm-plex-mono/400.css',
        '@fontsource/ibm-plex-mono/500.css',
        './src/styles/tokens.css',
        './src/styles/starlight.css',
      ],
      components: {
        SiteTitle: './src/components/starlight/SiteTitle.astro',
        Head: './src/components/starlight/Head.astro',
      },
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/jaylann/effaced' }],
      editLink: { baseUrl: 'https://github.com/jaylann/effaced/edit/stage/site/' },
      lastUpdated: true,
      plugins: [
        // Versioned docs (ADR 0017, amends 0011's deferral): stage HEAD is the
        // current "Latest" docs; '0.1' is the frozen snapshot of the released
        // effaced 0.1.0 / effaced-stripe 0.1.0 API. Cut a new snapshot per
        // minor/major release — see .claude/rules/docs.md.
        starlightVersions({
          current: { label: 'Latest' },
          versions: [{ slug: '0.1', label: '0.1' }],
        }),
      ],
      sidebar: [
        { label: 'Start here', items: ['docs', 'docs/quickstart'] },
        {
          label: 'Concepts',
          items: [
            'docs/concepts/annotations',
            'docs/concepts/erasure',
            'docs/concepts/saga',
            'docs/concepts/export',
            'docs/concepts/rectification',
            'docs/concepts/consent',
            'docs/concepts/restriction',
            'docs/concepts/retention',
            'docs/concepts/audit',
            'docs/concepts/resolvers',
            'docs/concepts/manifest',
          ],
        },
        { label: 'Guides', items: [{ autogenerate: { directory: 'docs/guides' } }] },
        {
          label: 'Project',
          items: [
            { autogenerate: { directory: 'docs/project' } },
            { label: 'Roadmap', link: '/roadmap/' },
          ],
        },
        {
          label: 'API reference',
          collapsed: true,
          items: [{ autogenerate: { directory: 'docs/reference' } }],
        },
      ],
    }),
  ],
});
