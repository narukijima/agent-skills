# Source policy and evidence ledger

## Purpose

Use this file before any SEO task. It defines how to choose current sources, record evidence, and keep folklore from becoming a production change.

## Evidence ledger

For every material observation, keep enough context to reproduce it:

| Field | Record |
| --- | --- |
| Target | property, environment, URL or URL population |
| Expected state | crawlable, indexable, canonical target, visible result, metric baseline |
| Observed state | raw value before interpretation |
| Source | code path, command, report, API, log, official documentation URL |
| Scope | one URL, sample rule, cohort, sitemap, whole property |
| Collection | user agent, device, static / rendered, field / lab, filters and aggregation |
| Time | timezone-aware timestamp and comparison windows |
| Limits | missing access, sampling, export cap, stale cache, unsupported dimension |

Preserve raw artifacts only where the Project permits it. Redact tokens, cookies, personal queries and private property data from reports and commits.

## Source hierarchy

Prefer, in order:

1. Project code, configuration, deploy and runtime output.
2. First-party site data: Search Console, analytics, CrUX / RUM, logs and crawl exports.
3. Current provider documentation and provider-published machine-readable ranges.
4. Web standards and vocabulary specifications.
5. Reproducible measurements.
6. Research with disclosed method, sample, dates and limitations.
7. Third-party analysis.
8. Community reports.

An official general statement does not override contradictory site-specific evidence; instead, investigate why the implementation differs. A third-party correlation never proves a provider ranking factor.

## Current-source procedure

For time-dependent claims:

1. Open the provider's current documentation, not a quoted copy.
2. Record the URL, page update date if available, and access date.
3. Identify the exact product and purpose covered.
4. Separate normative requirements, recommendations, examples and observed behavior.
5. Check changelog / deprecation notices when changing production behavior.
6. If the provider is silent, label the effect `unsupported`; do not convert absence into a promise.

Recheck at execution time: crawler names and purposes, IP ranges, robots behavior, rich-result eligibility, required properties, Search Console dimensions and limits, Core Web Vitals definitions / thresholds, AI feature controls, and experimental formats.

## Primary source directory

Use these as entry points, then follow the current product-specific page.

### Google Search

- Search documentation updates: <https://developers.google.com/search/updates>
- Search Essentials and spam policies: <https://developers.google.com/search/docs/essentials>
- Crawling and indexing: <https://developers.google.com/search/docs/crawling-indexing>
- Search Console documentation: <https://support.google.com/webmasters>
- Search Console API: <https://developers.google.com/webmaster-tools>
- AI features and generative AI guidance: <https://developers.google.com/search/docs/appearance/ai-features> and <https://developers.google.com/search/docs/fundamentals/ai-optimization-guide>
- Search status dashboard: <https://status.search.google.com/>

### Other search and AI providers

- OpenAI crawler roles and current IP lists: <https://developers.openai.com/api/docs/bots>
- Anthropic crawler roles and controls: <https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler>
- Perplexity crawler roles and current IP lists: <https://docs.perplexity.ai/docs/resources/perplexity-crawlers>
- Bing webmaster guidance and crawler documentation: <https://www.bing.com/webmasters/help/webmaster-guidelines-30fba23a>

Do not infer one provider's search backend, citation source, training policy or robots behavior from another provider's documentation.

### Standards and browser measurement

- Robots Exclusion Protocol: <https://www.rfc-editor.org/rfc/rfc9309>
- Sitemap protocol: <https://www.sitemaps.org/protocol.html>
- Schema.org releases and definitions: <https://schema.org/docs/releases.html>
- Schema.org validator: <https://validator.schema.org/>
- Web Vitals: <https://web.dev/articles/vitals>
- Chrome UX Report: <https://developer.chrome.com/docs/crux>

## Claim classification

- `confirmed`: direct target evidence or directly applicable current specification.
- `likely`: converging evidence with no direct causal observation.
- `hypothesis`: testable explanation awaiting measurement.
- `unsupported`: missing, stale, contradicted or causally invalid evidence.

When evidence conflicts, do not average confidence. Show the conflict, scope and the next discriminating test.

## Common evidence failures

- A cached or static fetch cannot prove rendered absence.
- `site:` results are a debugging clue, not a complete index count.
- A Search Console table may omit anonymized or non-top rows; preserve API / UI aggregation details.
- Average position is not a stable rank for every user or query.
- Browser success does not prove named crawler access through WAF / CDN.
- User-Agent strings can be spoofed; verify provider traffic using current official methods.
- A validator proves syntax or eligibility checks within its scope, not ranking or display.
- An SEO tool score is the tool's model, not a search-engine verdict.
- A before / after change without a control, time-series context or alternative hypotheses does not establish causality.

## Change authority

Read-only diagnostics may proceed within the user's scope. Do not treat access to code or dashboards as authorization to deploy, submit sitemaps, request removals, change WAF, alter robots, delete URLs or send external requests with side effects. Follow the Project's normal approval boundary.
