# Search quality and on-page diagnosis

## Start from the query and page role

Identify the query set, locale, device, search intent, business-critical page and current competing results. Use Search Console query-to-page data where possible; do not invent a target keyword from page copy alone.

Ask whether the URL provides the best distinct answer within the site's own inventory. Check cannibalization by evidence: multiple pages receiving impressions for the same intent, overlapping content, unstable selected URLs or internal signals. Similar keywords alone do not prove cannibalization.

## Page evidence

Inspect static and rendered output for:

- descriptive `<title>` and a clear, prominent visible page title
- snippet sources including page content and meta description
- headings that communicate structure rather than satisfy a count rule
- primary content availability, accuracy, provenance and freshness where relevant
- media alternatives, link purpose and accessible semantics
- language consistency and locale-specific content
- internal links to and from related user journeys

Judge text by usefulness and search-result behavior, not arbitrary character counts. Google documents no fixed `<title>` or meta-description length limit; result presentation truncates as needed. Multiple H1 elements are not automatically a ranking defect. A page can still have clarity or accessibility problems, but state the actual impact.

Current Google entry points:

- Title links: <https://developers.google.com/search/docs/appearance/title-link>
- Snippets: <https://developers.google.com/search/docs/appearance/snippet>
- SEO Starter Guide: <https://developers.google.com/search/docs/fundamentals/seo-starter-guide>

## Content and intent

Compare the page with the observed result set and user task:

- Does it answer the intent without forcing another thin intermediate page?
- Does it offer original utility, data, tools, experience or synthesis?
- Are claims accurate, attributable and current?
- Are important qualifications and limitations visible?
- Does the format match the task because it helps the user, not because of a supposed AI template?

There is no ideal word count, paragraph chunk size or keyword density. Search behavior, audience needs and subject complexity determine the appropriate form. Engagement metrics can diagnose user experience in the site's own analytics; do not label dwell time or bounce rate a direct ranking factor without current provider evidence.

## Architecture decisions

Map important user tasks to stable hubs and detail pages. Use the internal-link graph and actual navigation, not only URL strings. URL hierarchy, breadcrumbs and navigation may differ where that serves users; consistency and discoverability matter more than forcing a visual tree into every path.

Subfolder, subdomain and ccTLD choices have operational and product tradeoffs. Do not promise inherited authority or ranking advantage from one structure alone. Treat URL changes as migrations with redirects, parity checks and monitoring.

## Competitive and result analysis

Use current result pages and primary competitor sources to understand intent, available features, format and information gaps. Record locale, device, date, personalization limits and whether results are ads, organic links, rich results or AI features.

Do not copy a competitor's wording, page template or unsupported claims. A ranking competitor is evidence of what the engine currently serves for that query, not proof of a ranking factor. Separate:

- observed common pattern
- plausible user benefit
- provider-confirmed requirement
- experiment candidate

Use paid datasets if available, but label modeled traffic, authority scores and keyword estimates as vendor estimates.

## Finding thresholds

Create a Finding only when there is an expected-state mismatch and plausible impact. Examples:

- duplicate titles across a large template population that make results indistinguishable
- page title / content mismatch causing rewritten title links or wrong intent
- important content absent from rendered output
- orphaned business-critical pages with weak discovery evidence
- competing internal URLs splitting a query population with inconsistent canonicals / links

Do not create a Finding solely because a title exceeds a number, a page has two H1s, a URL is long, a heading level is skipped, or a tool marks a generic best-practice warning. Those may prompt accessibility, UX or maintainability review under the correct discipline.

## Fix and verify

Change the smallest template or data source that owns the confirmed pattern. Rebuild representative pages and verify source, rendered output, internal links and regressions across the population. For result presentation or ranking effects, mark implementation verified and measure later; do not guarantee that the engine will use the supplied title, snippet or page.
