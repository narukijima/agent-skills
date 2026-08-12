# Search measurement and drop diagnosis

## Measurement contract

Define before querying:

- property and canonical host scope
- timezone and complete date windows
- comparison: previous equivalent period and year-over-year when seasonality matters
- search type, country, device and locale
- query / page aggregation and filters
- business outcome joined outside Search Console, if permitted

Preserve raw totals separately from dimension tables. Search Console may omit anonymized queries and the API does not guarantee every row. Property and page aggregation calculate metrics differently; state which one was used.

## Traffic or ranking drop workflow

Do not begin with generic recommendations. Locate the break:

1. Validate that the reported drop is organic search, not analytics tagging, consent, channel grouping or site availability.
2. Compare complete periods and inspect the time-series breakpoint.
3. Split clicks and impressions. Stable impressions with fewer clicks suggests a different branch than both falling.
4. Sort loss by page and query; then split country, device, search type and search appearance where supported.
5. Determine whether the pattern is site-wide, directory / template, locale, device, query class or one high-volume page.
6. Compare brand / non-brand where available and appropriate; document any classifier limits.
7. Align the breakpoint with deploys, migrations, robots / canonical / sitemap changes, CDN / WAF, downtime, security / manual actions and Search status updates.
8. Check demand and seasonality with an equivalent market signal; do not blame an algorithm update solely because dates overlap.
9. Sample affected and unaffected controls through HTTP, rendered output, URL Inspection and Page Indexing.
10. Rank hypotheses by explanatory coverage and run the smallest discriminating test.

Current Google guides:

- Traffic-drop diagnosis: <https://developers.google.com/search/docs/monitor-debug/debugging-search-traffic-drops>
- Performance report data: <https://support.google.com/webmasters/answer/17011364>
- Search Analytics API: <https://developers.google.com/webmaster-tools/v1/searchanalytics/query>

## Search Console evidence

Use available first-party reports according to the question:

| Question | Evidence |
| --- | --- |
| When / where did traffic change? | Performance: date, query, page, country, device, search type |
| Does Google know the URLs? | Page Indexing by reason plus representative URL Inspection |
| Is crawl availability changing? | Crawl Stats response, host status, purpose and crawler type |
| Did sitemap processing change? | Sitemaps submitted / discovered counts, last read and errors |
| Are manual / security actions present? | Manual Actions and Security Issues |
| Is field performance poor? | Core Web Vitals URL groups and CrUX / RUM |

URL Inspection is page-level and may have quotas; use representative samples rather than claiming population coverage. Page Indexing totals exclude unknown URLs. Search performance is generally attributed to the provider-selected canonical, while Crawl Stats reports requested URLs; this difference is diagnostically useful.

Do not use the general Indexing API to submit ordinary pages unless current official documentation explicitly makes that content type eligible. Sitemap submission and recrawl requests are hints, not indexing guarantees.

## Metrics

- `clicks`: visits initiated from the measured search surface under that report's counting rules.
- `impressions`: appearances under product-specific visibility rules.
- `CTR`: clicks / impressions; interpret after query mix and presentation changes.
- `average position`: average topmost reported position, not a fixed rank or a user-level observation.

Focus on trends and affected populations. A better average position can coexist with less traffic if query mix or demand changes.

## Core Web Vitals and performance

Separate:

- `field`: real-user distributions from CrUX or first-party RUM over a time window.
- `lab`: controlled reproduction from Lighthouse, DevTools or WebPageTest.

Core Web Vitals are field metrics. Lab tools help reproduce and prevent regressions; they do not replace field evidence. Lighthouse cannot directly measure real-user INP in a synthetic load and may use diagnostic proxies. Recheck the current metric definitions and thresholds at <https://web.dev/articles/vitals>.

Workflow:

1. Identify affected URL groups, device and percentile / window in field data.
2. Reproduce representative pages in controlled lab conditions.
3. Attribute LCP, INP, CLS and TTFB to concrete resource, rendering, interaction or server causes.
4. Implement the smallest fix and run performance / functional regression tests.
5. Verify deployed lab output; keep field recovery pending until sufficient new data exists.

Do not optimize a single Lighthouse score while breaking content, analytics, accessibility or caching behavior.

## Search visibility measurement

Define visibility as an explicit metric rather than a marketing label. Possible measures include:

- Search Console impressions / clicks by owned query and page cohorts
- indexed / expected URL ratio by sitemap or template cohort
- share of observed result appearances in a fixed, disclosed query panel
- citation or mention rate in an AI response panel
- referral sessions and conversions from identifiable sources

Always include query set, locale, account / personalization state, model or product surface, date, repetition count and capture method. Search and AI outputs are stochastic and time-dependent; a one-off prompt is a screenshot, not a trend.

## AI citation / visibility study

Create a versioned prompt panel from real user tasks. Separate:

- source citation with a clickable URL
- brand mention without citation
- recommendation
- factual accuracy
- referral traffic

Run repeated observations across the selected product surfaces and preserve outputs within policy. Compare the same panel over time or against disclosed competitors. Do not treat citations as rankings, mentions as recommendations, or vendor visibility scores as provider-native truth. Avoid automated querying that violates provider terms.

## Verification status

Use explicit states:

- `verified_local`
- `verified_built_output`
- `verified_deployed_output`
- `pending_field_data`
- `pending_external_recrawl`
- `blocked_missing_access`

State the next observation date and owner for pending states. Never convert an expected delay into a successful result.
