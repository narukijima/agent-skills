# Programmatic SEO gates

Programmatic SEO is a data and product-quality problem before it is a page-generation problem. Do not generate the full population until every gate below has evidence.

## Gate order

### 1. Search demand

Define the repeating query pattern with first-party query data, current result research or a disclosed keyword dataset. Measure distribution, not only aggregate volume. A combinatorial variable list is not evidence of demand.

### 2. Search intent

Confirm that each query class expects a distinct result and that the proposed page completes the user task. Do not create intermediate doorway pages that only funnel users to the same destination.

### 3. Entity, identifier and dataset

Define the stable entity key, authoritative source, ownership / license, update frequency, missing-data behavior and deletion policy. Never treat scraped or licensed data as proprietary. Preserve provenance and terms.

### 4. URL population

Materialize the proposed URLs before pages. Deduplicate identifiers, normalize only according to an explicit Project policy, estimate growth and identify infinite / sparse combinations. Define redirects for renamed or merged entities.

### 5. Unique utility

For every eligible cohort, identify value that is specific to that entity or combination: trustworthy data, computation, availability, comparison, workflow, media, user contribution or original analysis. Variable substitution, generic prose and synonymized copies fail this gate.

### 6. Data quality

Measure completeness, freshness, accuracy, conflicts, coverage and null behavior. Define what happens when a page cannot meet its utility contract: do not publish, retire, consolidate or keep accessible without search indexing based on user need.

### 7. Template quality

Design from the user task. Keep title, visible heading, content, structured data and CTA consistent with real data. Test edge cases, not only the ideal record. There is no mandatory word count or schema type.

### 8. Internal linking

Connect pages through useful hubs, categories, related entities and pagination. Build and inspect the graph. A sitemap does not replace internal links, and a sitewide footer dump is not a substitute for relevant navigation.

### 9. Crawl strategy

Estimate discoverable URL volume, change rate, server capacity, facets / parameters and sitemap partitioning. Prevent crawl traps. Do not block URLs that must be crawled to observe `noindex` without understanding the provider behavior.

### 10. Indexation strategy

Define which cohorts should be indexable and why. Align status, canonical, robots, internal links and sitemap. Avoid shipping a huge population and using `noindex` as the primary quality-control system.

### 11. Measurement

Create cohort metrics for published, crawlable, discovered, crawled, indexed, served, clicked and converted URLs. Define thresholds, review dates and retirement rules before launch.

## Policy review

Read the current search-engine spam policies. For Google, check doorway abuse and scaled content abuse at <https://developers.google.com/search/docs/essentials/spam-policies>. Automation and generative AI are not inherently disallowed; large amounts of unoriginal content created primarily to manipulate ranking are the risk.

## Pilot before scale

Use a representative pilot across head, middle, long-tail and weak-data cohorts. Set an explicit maximum URL count for the pilot. Before expanding, verify:

- build and runtime correctness
- unique utility and data accuracy
- crawlable internal paths and sitemap integrity
- rendered canonical / robots / structured data
- server / CDN capacity and crawl behavior
- provider discovery / indexation evidence over an appropriate window
- engagement or conversion appropriate to intent

Expansion is a new decision, not the default outcome. A successful template test does not prove that 100,000 URLs deserve independent search results.

## Existing population diagnosis

When many generated pages are not indexed, join template cohort and data quality to:

- internal inbound links and depth
- sitemap membership and live status
- canonical / noindex / robots state
- provider Page Indexing reason and inspected examples
- content similarity and utility completeness
- crawl frequency and server errors

Do not assume crawl budget or thin content without this join. Improve or retire the failing cohort at its data / template owner; avoid page-by-page patches.

## Launch and rollback contract

Record dataset version, template revision, URL count, indexable cohort, sitemap files, monitoring owner and rollback / retirement plan. Preserve old URL history. If the population violates quality or overloads infrastructure, stop expansion and remove discovery signals deliberately; do not mass-delete without redirect / status analysis.

## Output

Return:

- evidence for each gate and a `pass`, `fail`, or `unknown` state
- proposed and pilot URL populations
- data / template / linking / crawl / index contracts
- policy risks and mitigations
- validation and expansion criteria

If demand, entity identity or unique utility fails, recommend not generating the population.
