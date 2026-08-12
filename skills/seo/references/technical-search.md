# Technical search protocol

## Route

Use the smallest population that can prove or disprove the suspected problem, then expand only when the pattern is real. Compare representative templates, important URLs, known failures and controls.

## HTTP and crawler access

Capture for browser and each relevant search crawler role:

- requested URL, final URL, status and complete redirect chain
- response headers including `X-Robots-Tag`, cache / CDN / WAF markers and retry signals
- robots.txt fetch status and matched group / rule
- HTML / resource accessibility required to render important content
- server, CDN and WAF logs with verified crawler identity where available

Treat `401`, `403`, repeated `429`, `5xx`, loops and robots fetch failures according to scope. A verified search crawler receiving `403` across the important population is a Critical crawl blocker; the same status on one intentionally private URL is not an SEO issue. Do not allow a User-Agent string through WAF without authenticating it by the provider's current official method.

Check differences caused by geography, IPv4 / IPv6, HTTP version, cookies, bot management, rate limiting, TLS, origin health and resource blocking. Avoid cloaking: the fix must not serve misleading content to crawlers.

## Robots and index controls

Evaluate separate layers:

1. robots.txt controls fetching, subject to each crawler's documented behavior.
2. `meta robots` and `X-Robots-Tag` are observed only when the crawler can fetch the resource.
3. authentication, WAF and network policies are access controls, not robots directives.
4. removal tools are temporary or product-specific controls, not substitutes for durable URL state.

Do not use robots.txt as a reliable noindex mechanism. Confirm directive precedence and product support in current official documentation. Record whether a directive is global or crawler-specific.

## Rendering and JavaScript

For content, links, canonicals, robots or JSON-LD that may be client-generated, compare:

- raw HTTP response
- parsed static DOM
- rendered DOM after required network / hydration settles
- provider-rendered HTML or URL Inspection when available

Record blocked scripts, failed API calls, consent state, timeout and viewport. A missing element in one layer is an observation about that layer, not proof of universal absence. Server rendering may reduce risk, but do not rewrite a working stack merely because JavaScript is present.

## Canonical diagnosis

Build a four-column comparison:

| Signal | Value |
| --- | --- |
| Requested | URL tested |
| Final | URL after redirects |
| Declared | HTML / HTTP canonical |
| Selected | provider-selected canonical when available |

Join internal links, sitemap membership, hreflang, content similarity and redirect targets. A self-canonical is usually coherent for a unique indexable page, but not mandatory proof that the engine will select it. Cross-canonicalization may be intentional for duplicates; confirm the expected owner before changing it.

Canonical conflict is confirmed when the Project's expected indexable URL points to another target or signals disagree across a material population. Verify both source and rendered output after a fix, then wait for external recrawl separately.

Current Google entry points:

- <https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls>
- <https://developers.google.com/search/docs/crawling-indexing/canonicalization-troubleshooting>

## Sitemap integrity

Parse sitemap indexes recursively within protocol limits and join every listed URL to live evidence. A search sitemap should normally contain intended canonical, indexable URLs that return a successful response.

Flag populations of:

- redirect, `4xx`, `5xx`, soft-404 candidates
- `noindex` or blocked URLs
- declared canonical to another URL
- environment, protocol, host or locale leakage
- malformed, duplicate or stale entries
- URLs present in the expected population but absent from discovery paths

Do not assume submission guarantees crawl or indexation. Compare submitted / discovered counts, last read / errors, Page Indexing states, and expected URL population. Use `scripts/seo_evidence.py audit-inventory` when a crawler export is already available.

### Deterministic evidence helper

The helper does not crawl or decide root cause. It accepts a JSON array, `{ "records": [...] }`, or JSON Lines. Every record requires `url`; supported observations include `status`, `final_url`, `redirect_chain`, `robots_allowed`, `meta_robots`, `x_robots_tag`, `canonical`, `expected_canonical`, `expected_indexable`, `in_sitemap`, `crawler_role`, `scope`, and `soft_404`.

```bash
python3 skills/seo/scripts/seo_evidence.py audit-inventory --input inventory.json --fail-on High
python3 skills/seo/scripts/seo_evidence.py extract-html --url https://example.test/page --html saved.html
```

`crawler_role: "search"` and `scope: "site"` must come from a verified measurement, not a guessed User-Agent. The output marks every signal `evidence_state: observed` with `severity_candidate` and `needs_diagnosis`; a signal is a recorded observation, not a `Confirmed` diagnosis on the Skill's claim scale. The Agent still confirms population, impact, root cause and final Finding.

## Index-state diagnosis

Keep these states distinct: generated, discoverable, crawled, rendered, canonicalized, indexed and served. Compare expected URL count with sitemap, internal graph, crawl logs and provider reports; no single count represents all stages.

Investigate examples from each provider-reported reason, not only the headline count. Confirm soft 404s against content and purpose. Treat "discovered", "crawled", "alternate" and "duplicate" as observations needing page-level evidence, not generic fix labels.

## Architecture and internal links

Build a directed graph from rendered, crawlable links. Measure:

- inbound link count and sources for important pages
- orphan candidates against all known URL inventories
- shortest path and depth distribution from relevant entry hubs
- broken / redirected internal links
- pagination and infinite-scroll discoverability
- faceted / parameter combinations and crawl traps
- hub coverage, contextual relevance and anchor clarity

There is no universal three-click or link-count ranking rule. Depth is evidence when important pages are materially harder to discover or receive weaker internal support than comparable pages. Prefer links that help users navigate; do not add sitewide keyword links solely to manipulate ranking.

## Facets, parameters and crawl traps

Enumerate dimensions and allowed combinations before changing controls. Quantify URL population growth, crawl frequency and indexed utility. Choose among link suppression, parameter design, canonicalization, noindex, robots controls and URL retirement based on desired discovery / indexing behavior; these controls are not interchangeable.

Test pagination without relying on deprecated hints. Ensure important items can be reached through crawlable URLs and links without requiring user gestures only.

## International search

For each locale / region cluster verify:

- stable, crawlable URL per variant
- declared language / region codes and current provider syntax
- reciprocal alternates including the current page where required
- canonical and hreflang alignment
- `200` indexable targets without redirect chains
- consistent HTML, HTTP-header or sitemap implementation
- actual localized visible content and user routing behavior

Do not redirect solely by IP or inferred language in a way that hides variants from crawlers or users. Do not assume subdirectories always rank better than subdomains or ccTLDs; select structure using ownership, operations, geotargeting, migration cost and provider guidance.

Current Google entry point: <https://developers.google.com/search/docs/specialty/international/localized-versions>

## Migrations

Before launch, freeze old and new URL inventories, content / metadata parity, redirect mapping, canonical / hreflang rules, analytics and monitoring. Test representative and bulk mappings in preview.

After launch, compare:

- old URL → final new URL mapping and chain length
- status / canonical / robots / metadata parity
- sitemap and internal-link cutover
- crawl / indexing / traffic timelines
- CDN / WAF / DNS / origin changes

Do not combine unrelated domain, CMS, design, content and URL changes without accepting reduced diagnostic power. Preserve rollback or forward-fix ownership.

## Completion evidence

Report local code, built artifact, deployed HTTP / rendered output and provider recrawl as separate surfaces. If production cannot be inspected, the result is locally validated, not deployed-verified.
